# Začnite tukaj

**V enem stavku:** ta sistem zbira dokumentacijo o skladnosti vaših
dobaviteljev, jo poveže z vašimi artikli in vam pove, kaj manjka.

**Stanje:** V uporabi. Preverjeno na delujočem zaslonu 14. 9. 2026.

---

## Kaj sistem naredi za vas

Prodajate medicinske pripomočke, ki jih niste izdelali sami. Za vsakega od
njih zakon zahteva, da preverite, ali je proizvajalčeva dokumentacija v
redu — izjava o skladnosti, navodila za uporabo, številka priglašenega
organa. Tega ročno za šestnajst tisoč artiklov ni mogoče narediti.

Zato to naredi sistem. Sistem:

1. Prebere vaš seznam artiklov iz Business Centrala.
2. Za vsakega dobavitelja poišče njegove dokumente — na njegovi spletni
   strani, v EUDAMED, v vašem poštnem predalu, v arhivu, ki ga že imate.
3. Prebere vsak dokument in ugotovi, kaj je, kdaj je bil izdan in katere
   vaše artikle pokriva.
4. Tiste, pri katerih je prepričan, sam vloži.
5. Pri tistih, pri katerih ni prepričan, **se ustavi in vpraša vas**.

Korak 5 je vaše delo. Ves ta vodnik govori o tem, kako korak 5 opraviti
dobro.

---

## Kaj sistem nikoli ne bo naredil

- **Nikoli ničesar ne izbriše.** Vsak dokument, ki ga je pridobil, ostane v
  arhivu, ne glede na to, kaj kdo o njem odloči.
- **Nikoli ne pošlje e-pošte.** Zahtevke za obnovitev napiše namesto vas;
  pošlje jih oseba.
- **Nikoli ne objavi dokumenta, ki ste ga zavrnili.**
- **Nikoli ne spremeni odločitve, ki ste jo sprejeli.** Ob vsaki je
  zabeleženo vaše ime in čas.

---

## Prijava

Odprite naslov, ki vam ga je dal vaš IT-kontakt. Brskalnik vas bo, preden
karkoli vidite, vprašal za uporabniško ime in geslo.

To so edina vrata noter. Uporabniško ime je vaše, ne skupno, zato sistem ob
vsaki vaši odločitvi zabeleži vaše ime.

Če vidite zaslone, lahko počnete vse, kar je v tem vodniku. Nekaj pritiskov je
pridržanih za tistega, ki poganja stroj, in vsi so na zaslonih, ki jih ta vodnik
od vas ne zahteva. Če pristanete na enem od njih, glejte **Če zaslon sporoči, da
je šlo nekaj narobe** spodaj.

Brez prijave isti sistem uporabljata še dve stvari, ki ju boste morda
slišali omenjati:

- **Povezava na kartici artikla v Business Centralu.** Odpre kartico samo za
  branje, ki prikazuje, kaj je na voljo za ta artikel.
- **Spletna trgovina.** Samodejno bere objavljene dokumente, da jih lahko
  pokaže strankam.

Nobena od njiju ne more ničesar spremeniti.

---

## Kako se znajti

### Meni na levi

Tri skupine, v tem vrstnem redu, in četrta, ki jo lahko prezrete.

| Skupina | Kaj vsebuje |
|---|---|
| **Your work** | Štiri vrste in zaslon, ki jih našteje: **Today**, Review, Missing documents, Expiring, Renewal emails. Vsaka od štirih nosi svoje število |
| **Records** | Kar je shranjeno, za iskanje podatkov: Items, Documents, Manufacturers, EUDAMED checks, Emails received |
| **Add** | Upload a document, Import from Business Central |
| **Operator** | Zložen blok na dnu: System status, Failed tasks, Queues & health, Playbooks, Data quality, Decisions log, Scheduler, Business Central push, API. Poganjanje stroja, ne opravljanje dela. Je tam le, če vaša prijava poganja stroj, zato ga morda sploh ne vidite |

Na kratko: **prve tri skupine so vaše delo. Blok Operator je delo stroja.**
Večino dni potrebujete le **Today** in tisto, kamor vas z njega pošlje.

Šest zaslonov ni v meniju; dosežete jih z zaslona, ki jim pripada: **Coverage
gaps** in **Discovery** iz vrstice povezav na Items, **EUDAMED ID (SRN) queue**
in **Check due** z zaslona EUDAMED checks, **Onboard a supplier** s Playbooks
ter **Weekly reports** s Today.

### Iskalno polje

Nad menijem. Vnesite številko artikla, številko certifikata ali ime
dobavitelja. Sistem hkrati preišče artikle, dokumente in proizvajalce ter
prikaže prvih nekaj zadetkov vsakega.

Ne ugiba in ne popravlja črkovanja. Če ne dobite ničesar, poskusite s
krajšim delom številke.

### Števila v meniju

Štiri postavke pod **Your work** nosijo številko, vsaka je število zaslona, ki
ga odpre:

| Postavka | Pomeni |
|---|---|
| **Review** | Dokumenti, ki čakajo na vašo odločitev |
| **Missing documents** | Artikli, za katere je sistem iskal in ni našel ničesar |
| **Expiring** | Certifikati, ki so potekli v zadnjih 180 dneh ali potečejo v naslednjih 30. Šteje se en certifikat, ne vsak dokument |
| **Renewal emails** | Dopisi za podaljšanje, napisani za vas in še neposlani |

Naložijo se trenutek za stranjo in se same osvežujejo vsakih nekaj sekund, zato
zaslon, ki je odprt vse dopoldne, še vedno kaže današnje številke. Pomišljaj
pomeni, da še niso prispele.

### Vrstica o zdravju na dnu menija

Ena vrstica: **● System working** ali imena delov sistema, ki se ne odzivajo
več. Če ni prve možnosti, sistem deluje le napol — povejte razvijalcu. To ni
nekaj, kar lahko popravite s teh zaslonov.

Kadar so spletne strani dobaviteljev potekle v časovni omejitvi, vrstica to
pove in ponudi **details**, kar odpre [Failed tasks](pages/failed.sl.md). To pa
lahko rešite: isti gumb vam ponudi tudi Today.

---

## Today

Prvi zaslon, na katerega pridete, in tisti, na katerega se vračate. Štirje
seznami, vsak s svojim številom, enim stavkom in enim gumbom: dokumenti za
pregled, manjkajoči dokumenti, certifikati pred potekom in dopisi za
podaljšanje. Pod njimi do tri vrstice o delu, ki ni uspelo; dve od njih lahko
ponovite z enim pritiskom. Nato stavek **Declarations on file for X of Y
medical-device items (Z%)**, številka, ki jo navedete Dentalii, z vrstico pod
njo, ki pove točno, kaj šteje. Na koncu zadnji dve tedenski poročili.

Celotna navodila: [Today](pages/today.sl.md).

Nadzorna plošča stroja je še vedno tu kot
[System status](pages/status.sl.md), v bloku Operator na dnu menija. Skoraj nič
na njej ni vaše delo.

---

## Ko nekaj pritisnete

Vsak gumb na teh zaslonih odda delo sistemu in vam odgovori z enim stavkom.
Stavek pove, kaj se **bo** zgodilo, ne da je že narejeno — z odobritvijo
dokumenta gre odločitev v vrsto, register pa jo ujame v minuti:

| Kaj ste pritisnili | Kaj odgovori |
|---|---|
| **Approve** | *Approval recorded. The registry updates within a minute.* |
| **Reject** | *Rejection recorded. The document stays on file and can be reopened from its own page.* |
| **Search again** | *The system will search again for this item…* |
| **Upload** | *Document received. The system reads it…* |

Dvoje je pri teh odgovorih vredno vedeti:

- **"Already in progress."** pomeni, da je isto delo že v vrsti — vaš pritisk ni
  spremenil ničesar, ker ni bilo česa spremeniti. Dvojni pritisk je povsod v tem
  sistemu varen.
- **"Technical details"** pod odgovorom odpre številko opravila za njim.
  Namenjena je razvijalcu. Vi je ne potrebujete.

---

## Če zaslon sporoči, da je šlo nekaj narobe

Dobite stran, ne kupa kode. V eni vrstici pove, kaj se je zgodilo, meni na levi
ostane, ponudi pa tudi **Go to Today**, tako da nikoli ne obtičite.

| Kaj piše | Kaj storite |
|---|---|
| **Page not found** | Naslov je napačen ali zastarel. Pritisnite **Go to Today** in pridite tja prek menija |
| **You cannot open this** | Ta zaslon ali ta gumb ni del vaše prijave. Nič ni pokvarjeno. Če piše *This action is for operators*, prosite tistega, ki poganja stroj, naj to opravi |
| **Something went wrong** | Niste vi povzročili in nič se ni spremenilo. Poskusite znova; če se ponavlja, razvijalcu povejte, kateri zaslon in kateri gumb |

Kar koli piše na strani, **nič ni bilo zapisano**. Zavrnjeno dejanje je
zavrnjeno dejanje, ne na pol opravljeno.

---

## Kako izgleda slab dan

Ničesar vam ni treba diagnosticirati. Pomaga pa vedeti, kaj od tega je vaše
in kaj razvijalčevo.

| Kaj vidite | Čigav problem |
|---|---|
| Veliko dokumentov v Review | **Vaš.** Sistem je našel stvari in potrebuje odločitve |
| Stvari v Missing documents | Večinoma **vaš**. Naložite, kar najdete, ali naročite ponovno iskanje |
| Vrstice pod *Failed tasks* na Today | Prvi dve sta **vaši**, vsaka z enim gumbom. Tretja, *waiting for the developer*, ni |
| Vrstica o zdravju, ki navede del sistema | Razvijalčev. Prijavite |
| Coverage pada | Vredno vprašati. Navadno je dobavitelj prenehal objavljati ali pa je prispela nova serija artiklov, za katero dokumentacije še ni |
| Zaslon, ki prikaže besede, kot sta `C5` ali `gate.apply` | Kozmetično. Prezrite jih — glejte [slovar](glossary.sl.md#kode-ki-jih-lahko-prezrete) |

---

## Kam naprej

- [Today](pages/today.sl.md) — zaslon, na katerega pridete
- [Vaš dnevni krog](01-daily-work.sl.md) — kaj storiti vsako jutro
- [Review](pages/review.sl.md) — zaslon, ki ga boste uporabljali največ
- [Slovar](glossary.sl.md) — vse besede na enem mestu
