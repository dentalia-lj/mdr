# Expiry

**V enem stavku:** katera dokumentacija o skladnosti je že potekla in katera
bo kmalu potekla.

**Stanje:** Deluje. Preverjeno glede na kodo, 15. 9. 2026.

**Ta stran je samo za branje.** Nič na njej ne zapiše, ne pošlje in ne
spremeni nobenega vašega dokumenta.

---

## Kaj je to

Trije seznami, ne eden: kaj je že poteklo, kaj poteče kmalu in — umaknjeno s
poti, a ne skrito — kaj je poteklo dlje nazaj kot to. Nekoč je en sam seznam
mešal "že pokvarjeno" z "bo kmalu pokvarjeno", kar je zakopalo najbolj nujne
vrstice.

Vsaka vrstica je datum enega certifikata ali izjave, tudi kadar več vaših
dokumentov navaja isti datum, s tem, koliko dokumentov ga navaja in koliko
izdelkov izgubi objavljeno pokritost, ko datum mine.

Okni se začneta pri **180 dneh nazaj** in **30 dneh naprej**: poteklo v
zadnjih 180 dneh in poteče v naslednjih 30 dneh. 30 dni naprej je edino okno za
potek, ki ga uporablja ves sistem, in ga navajajo [Today](today.sl.md),
tedensko poročilo in ta stran.

Številka ob **Expiring** v meniju je seštevek prvih dveh od teh treh seznamov
pri teh dveh nastavitvah, šteta na enak način: en certifikat, ne vsak dokument.
Meni, Today in ta stran vam torej ne morejo pokazati različnih številk. Kar je
poteklo davno, je iz tega števila namenoma izpuščeno: to je stalna
izpostavljenost, ki jo je treba znati dokazati na zahtevo, ne delo za ta
teden.

Pod tremi seznami je primerjava z EUDAMED: certifikati, ki jih ta baza
podatkov prikazuje kot umaknjene ali začasno preklicane, certifikati,
katerih podatki se ne ujemajo s tem, kar hranite vi, in certifikati, ki jih
EUDAMED navaja, a vi zanje nimate niti ene kopije.

---

## Kdaj to uporabite

- Tedenski pregled, kaj je treba poganjati.
- Odločitev, katerega dobavitelja poklicati naslednjega glede obnovitve.
- Preverjanje, ali za poteklo izjavo že obstaja začeta zahteva za obnovitev.

---

## Preden začnete

Nič.

---

## Kaj storite

1. Kliknite **Expiring** v meniju na levi ali **See which** na zaslonu
   [Today](today.sl.md).
2. Če ni prazno, najprej preberite **Review due**. Ti dokumenti niso potekli:
   izjava o skladnosti sama po sebi nima datuma veljavnosti, zato jo Dentalia
   uvrsti sem pet let po izdaji. Nič ni poteklo in noben izdelek ni izgubil
   kritja — dobavitelja prosite, naj potrdi, da izjava še velja, ne pa da jo
   obnovi. Izdelki razreda I sploh nimajo certifikata, zato je to edino
   opozorilo, ki ga bodo kdaj sprožili.
3. Pod **Review due** je za vsakega dobavitelja gumb **Ask**. Napiše pismo, v
   katerem ga prosite, naj potrdi, da izjave še veljajo — ne pa da jih obnovi,
   saj ni nič poteklo. Eno pismo pokrije vse njegove izjave s seznama. Nič se
   ne pošlje: osnutek počaka na zaslonu **Renewal emails**, da ga preberete,
   naslovite in pošljete.
4. Preberite **Expired in the last 180 days** — to je živo, nerešeno delo,
   in vsak datum tukaj je datum, ki ga navaja dokument ali njegov certifikat.
5. Preberite **Expiring in the next 30 days** za to, kaj poganjati, preden
   postane nujno. Če je prazno, vrstica pod njim pove, kako daleč je naslednji
   datum.
6. Spremenite **Expired within** ali **Expiring within** na 30, 90, 180 ali
   365 dni in pritisnite **Filter**, da razširite ali zožite eno od obeh
   oken.
7. Po potrebi odprite **Long expired** za vse, kar je starejše, kot pokriva
   prvo okno.
8. Kliknite **detail** ob vrstici, da odprete sam dokument.
9. Poglejte stolpec **Chase** — barvna oznaka tam pomeni, da zahteva za
   obnovitev za tega dobavitelja že obstaja; kliknite jo, da jo vidite.
10. Pomaknite se navzdol do primerjave z EUDAMED za enako vrsto preverjanja
   pri vseh dobaviteljih naenkrat, ne le pri tistih z bližajočim se datumom.

---

## Kaj se zgodi nato
Na tej strani se ne spremeni nič. Pove vam le, kaj je treba poganjati —
poganjanje samo se zgodi z elektronskim sporočilom dobavitelju, iz
[Renewal emails](drafts-out.sl.md) ali ročno.

---

## Kaj lahko gre narobe

| You see | Pomeni | Kaj storiti | Dobro ali slabo |
|---|---|---|---|
| A row shaded more strongly than the others | Ta vrstica potrebuje pozornost zdaj — je bodisi že potekla, bodisi poteče v naslednjih 30 dneh | Poglejte jo najprej | Slabo, zahteva poganjanje |
| A **Documents** count higher than 1 on one row | Več vaših dokumentov navaja datum istega certifikata | Nič dodatnega za storiti — en poziv dobavitelju pokrije vse hkrati | Nevtralno |
| A date marked *(review)* | Ni resničen datum poteka — glejte petletno pravilo | To razumite kot poziv, naj preverite pri dobavitelju, ne kot kršitev | Nevtralno |
| A date marked *(inherited)* | Ni lasten datum izjave — prevzet je od certifikata, na katerega se sklicuje | Nič dodatnega za storiti | Nevtralno |
| *N further certificates expire beyond this window* | Obstaja več datumov poteka, kot jih prikazuje trenutni filter | Kliknite **Show everything**, če jih želite videti vse | Nevtralno |
| No coloured label in the **Chase** column | Za tega dobavitelja še nihče ni začel zahteve za obnovitev | Premislite o začetku ene iz Renewal emails | Odvisno od nujnosti vrstice |

---

## Besede, ki jih uporablja zaslon

| The screen says | Pomeni |
|---|---|
| *(review)* / *(inherited)* next to a date | Glejte [petletno pravilo](../glossary.sl.md) |
| **Type**, prikazano kot *Declaration of Conformity*, *EC certificate*, *Instructions for use*, *ISO certificate* | Kakšna vrsta dokumenta nosi datum poteka |
| **Documents** | Koliko vaših shranjenih dokumentov navaja ta datum poteka |
| **Items affected** | Koliko izdelkov izgubi objavljeno pokritost, ko ta datum mine |
| **Revision drift** | EUDAMED za certifikat navaja drugačno različico, kot je na dokumentu, ki ga hranite vi |
| **Certificate status alerts** | EUDAMED sam prikazuje certifikat kot umaknjen, preklican, začasno prekinjen ali omejen |

---

## Sorodno

- [Documents](documents.sl.md) — odprite kateri koli dokument, naveden na tej tabli
- [Manufacturers](manufacturers.sl.md) — ista primerjava z EUDAMED, za enega dobavitelja naenkrat
- [Glossary](../glossary.sl.md) — vse o pravilu 30 dni in petletnem pravilu
