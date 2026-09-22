# Review

**V eni povedi:** tu odločite, ali dokument dobavitelja šteje.

**Stanje:** V uporabi. Preverjeno glede na kodo, 11. 9. 2026.

---

## Ali lahko tu kaj pokvarim?

Ne.

- Nič na tem zaslonu ne izbriše dokumenta. Nikoli. Vsaka datoteka, ki jo je
  sistem pridobil, ostane na voljo, ne glede na vašo odločitev.
- **Approve** pomeni, da dokument šteje. **Reject** pomeni, da ne šteje. To je
  edina razlika.
- Odločate lahko le pri odprtem dokumentu. Zaprta vrstica ima en sam gumb,
  **Open**, in nič drugega.
- Odobritev, ki seže na celoten program dobavitelja, vas najprej enkrat
  vpraša in pove, koliko artiklov bo zajela.
- Ste si premislili? Odprite dokument pod **Documents** in odločite znova.
- Vsaka odločitev se shrani z vašim imenom in časom. Pri zavrnitvi se shrani
  tudi razlog, ki ste ga izbrali.

Če gumb vrne napako, se ni zgodilo nič. Dokument je natanko tak, kot je bil.
Poskusite znova ali vprašajte razvijalca.

---

## Čemu ta zaslon služi

Sistem sam išče dobaviteljevo dokumentacijo — izjave o skladnosti, certifikate,
navodila za uporabo. Večino tega, kar najde, tudi sam vloži.

Kadar ni prepričan, se ustavi in dokument postavi sem. V vaše evidence ne doda
ničesar, dokler se s tem ne strinja človek.

Torej: prazen zaslon Review pomeni, da je sistem prepričan glede vsega, kar je
našel. Poln zaslon pomeni, da potrebuje vas.

---

## Vrste vrstic

| Kaj vidite | Kaj vas sistem sprašuje |
|---|---|
| **Dokument** z gumbom **Open**, pod imenom svojega proizvajalca | "Našel sem to. Naj šteje?" |
| **Predlog za združitev v skupino** | "Mislim, da sta ta dva izdelka ista družina. Sem prav ugotovil?" |
| **Links waiting for review on published documents**: artikel in dokument z gumboma **Covers this item** in **Does not** | "Ta objavljeni dokument je s tem artiklom povezan le po šibkem ujemanju. Ga pokriva?" |
| **Links waiting on documents that were never published** | Nič. Ta seznam je zgolj v vednost. Ni gumba in ni ničesar za odločiti |

Večino dni se boste ukvarjali le s prvo vrsto.

---

## Kako odločati, v treh vprašanjih

Odprite PDF in se vprašajte:

1. **Ali gre za pravo vrsto dokumenta?** Izjava o skladnosti, certifikat,
   navodila za uporabo — in ali dejansko prihaja od dobavitelja, za katerega se
   izdaja?
2. **Je aktualen?** Poglejte datume. Če za iste izdelke že imate novejšega, ta
   ne sme nadomestiti tistega.
3. **Ali pokriva prave izdelke?** Bodisi navaja številke artiklov, ki jih
   prepoznate, bodisi pravi, da pokriva celoten program dobavitelja.

Trikrat da: odobrite. Kakršenkoli ne: zavrnite ali vprašajte dobavitelja.

---

## Korak za korakom

1. Kliknite **Review** v meniju na levi.
2. Seznam je razvrščen po proizvajalcih. Vsaka skupina se začne z vrstico, kot
   je *GC 12 documents*, najprej pa pridejo skupine z najstarejšim čakajočim
   dokumentom. Dokumenti, ki jim nihče ni znal pripisati proizvajalca, so na
   koncu, pod **Manufacturer not known**. Znotraj skupine je najstarejši
   dokument na vrhu. Številka ob naslovu, *N waiting in all*, šteje vse, kar
   čaka, ne glede na to, kaj ste filtrirali. Kadar je vklopljen gumb razloga
   ali iskanje, vrstica nad seznamom pove, koliko od teh je ostalo, števci
   skupin in števec strani pod seznamom pa štejejo samo te.
3. Vsaka vrstica najprej poimenuje dokument, na primer *GC · Declaration of
   Conformity (MDR)*. Pod tem so ime datoteke, datum izdaje in datum, od
   katerega čaka, nato pa siva poved, ki v eni vrstici pove, zakaj se je sistem
   ustavil. Pri dokumentu, ki mu je veljavnost potekla, piše še *expired* in
   datum. Če nekateri artikli, povezani z dokumentom, pripadajo drugemu
   dobavitelju, kot je skupina, v kateri je dokument, vrstica to pove:
   *+1 other manufacturer among its items*.
4. V vrstici pritisnite **Open** (ali kliknite kjerkoli nanjo). Vrstica se
   odpre v dveh polovicah. Levo je prva stran samega PDF-ja; **Open full size
   ↗** odpre cel dokument v novem zavihku. Desno je tisto, kar je sistem
   prebral iz njega: **Manufacturer** (kot je natisnjen na dokumentu),
   **Rules** (MDR ali MDD), **Issued**, **Valid until** in **Basic UDI-DI**.
   Datum, ki ga dokument ne navaja, se glasi *Not stated*.
5. Nad gumbi plošča pove, kaj bo odobritev naredila: *Approving makes it count
   for these N items*, sledijo pa številka in ime vsakega artikla (prvih deset;
   za ostale pritisnite **+ K more**) in kako so bili ujeti. Pri dokumentu, ki
   pokriva celoten program dobavitelja, piše *…for all N ⟨dobavitelj⟩ items*,
   oziroma *…for the one ⟨dobavitelj⟩ item*, kadar ima dobavitelj en sam
   artikel s pripomočkom.
6. Primerjajte PDF z dejstvi ob njem: vrsto dokumenta, datume, artikle.
7. Pritisnite en gumb:

| Gumb | Kaj naredi |
|---|---|
| **Approve for these N items** (**Approve for this item**, kadar je artikel en sam) | Dokument od zdaj šteje, in sicer natanko za artikle, navedene nad gumbom. Pojavi se pod Documents, vsak od teh artiklov pa ga zdaj prikazuje |
| **Approve** | Enako, le da odobritev še ne seže do nobenega artikla. Plošča pove, zakaj: dokumenta ne povezuje z artiklom nič, ali pa je povezan le po šibkem ujemanju (glejte spodaj) |
| **Reject…** | Najprej vpraša, zakaj. Izberite enega od šestih razlogov spodaj, po potrebi dodajte opombo, nato pritisnite **Reject**. Dokument ne šteje. Ostane na voljo, označen kot zavrnjen, in ga je mogoče znova odpreti. Vaše ime in razlog se zabeležita, razlog pa je viden na strani [Decisions](decisions.sl.md). Sistem bo naprej iskal boljšega. **Cancel** zapre razloge in ne spremeni ničesar |
| **Correct a fact first** | Odpre polja za vrsto dokumenta, pravila, datume in številko certifikata. Spremenite le tisto, kar je na dokumentu napačno; popravki se shranijo, ko odobrite. Pri dokumentu za celoten program ga ni, ker se popravek tam ne bi shranil |
| **Approve for all N items** | Pojavi se le, kadar dokument pokriva celoten program dobavitelja namesto poimensko navedenih izdelkov. Najprej izberite dobavitelja s seznama, nato pritisnite gumb. Seznam ponuja **vse** dobavitelje iz kataloga. Gumb še ne odobri: zaslon vpraša *Approve for all N ⟨dobavitelj⟩ items?* in razloži, kaj to pomeni. Za nadaljevanje pritisnite **Yes, approve for N items**, s **Cancel** pa ne spremenite ničesar. Če je N enak 0, imajo vsi izdelki tega dobavitelja v Business Centralu prazen razred pripomočka — z odobritvijo se dobavitelj vseeno zabeleži, izdelki pa se pripnejo, ko nekdo ta razred izpolni |

8. Števec na vrhu zaslona (*N waiting in all*) se zmanjša za eno. Nadaljujte z
   naslednjo vrstico.

### Kadar plošča pravi, da artikli še ne bodo šteli

Nekateri artikli so z dokumentom povezani le po šibkem ujemanju: po tem, kje je
bila datoteka najdena, po podobnem imenu izdelka ali po številki artikla brez
potrjenega proizvajalca. Odobritev dokumenta zanje ne velja. Plošča jih navede
posebej in zapiše *Approving publishes it, but it will not count for any item
yet* (ali *It is also linked to N more items…*, kadar nekateri artikli štejejo).
Po odobritvi vsak od teh artiklov čaka pod **Links waiting for review on published
documents**, niže na zaslonu Review: za vsakega pritisnite **Covers this item**
ali **Does not**.

### Prikaz ene vrste razloga naenkrat

Gumbi nad seznamom pokažejo dokumente, ki čakajo zaradi ene vrste razloga.
**All reasons** spet pokaže vse.

| Gumb | Pokaže dokumente, pri katerih |
|---|---|
| **Which items is unclear** | Sistem ni mogel ugotoviti, katere vaše artikle dokument pokriva. Sem sodijo tudi dokumenti, pri katerih se siva poved začne z *Read without problems* |
| **Manufacturer unclear** | Sistem ni mogel potrditi, čigav je dokument |
| **Covers a whole range** | Dokument pokriva vse, kar dobavitelj izdeluje |
| **Expired** | Datum *Valid until* je že mimo |
| **Other** | Vse drugo: vrsta dokumenta, njegovi datumi, certifikat, ki ga nimamo |

Dokument z dvema sporočiloma se lahko pojavi pod dvema gumboma. **Expired** je
datum in ne sporočilo, zato seka vse druge: dokument s poteklo veljavnostjo je
pod **Expired** in hkrati pod gumbom, ki mu pripada njegovo sporočilo, tudi
dokument za celoten program. Iskanje v polju nad seznamom ohrani izbrani gumb,
gumb pa ohrani vaše iskanje.

### Šest razlogov za zavrnitev

| Razlog | Kdaj ga uporabite |
|---|---|
| **Wrong manufacturer** | Dokument je od drugega podjetja, kot trdi vrstica |
| **Not one of our items** | Dokument je pravi, a za izdelke, ki jih ne vodimo |
| **Not a compliance document** | Gre za prospekt, cenik, varnostni list ali karkoli drugega, kar ni izjava, certifikat ali navodila |
| **Out of date** | Obstaja novejša različica ali pa je ta že na voljo |
| **Duplicate of another document** | Isti dokument je že na voljo |
| **Other** | Nič od naštetega. V opombi napišite, kaj |

Opomba ni obvezna in je lahko dolga največ 500 znakov. Kar napišete, se shrani
ob razlogu, na primer *Out of date: newer 2025 version exists*.

---

## Sporočila, ki jih boste videli, in kaj storiti

Siva poved v vsaki vrstici je sistemova razlaga, zakaj se je ustavil. Teh je
trinajst. Razvrščena so v tri skupine.

### Običajno v redu — preberite PDF, nato odobrite

| Zaslon pravi | Kaj to pomeni | Kaj storiti |
|---|---|---|
| *This replaces an older document already on file.* | Sistem je že ugotovil, da je to novejši dokument | Odobrite. To je sistem, ki opravlja svoje delo |
| *The document does not say which items it covers.* | Nič na strani ne navaja številke artikla | Zelo pogosto pri izjavah za celoten program. Uporabite **Approve for all N items** in izberite dobavitelja |
| *This document names a certificate we do not have on file yet.* | Sklicuje se na certifikat, ki ga še nimamo | Če je dokument sicer v redu, ga odobrite. Manjkajoči certifikat poiščite ločeno |
| *This does not look like a medical device document, so it was held back from automatic filing.* | Ni navedbe MDR ali MDD in ni certifikata kakovosti — lahko gre za izjavo o strojih, kozmetiki ali elektriki | Odprite PDF. Če dokument res govori o pripomočku, ga odobrite; sicer ga arhivirajte, da se tu ne pojavlja več |
| *The article numbers point at more than one group of items.* | En dokument pokriva izdelke, ki jih vodimo v ločenih družinah | Odobrite, če resnično pokriva vse |

### Najprej natančno preglejte PDF

| Zaslon pravi | Kaj to pomeni | Kaj storiti |
|---|---|---|
| *We could not confirm which manufacturer issued this.* | Ime na dokumentu se ne ujema z ničimer v Business Centralu | Običajno pri novem dobavitelju. Izberite pravega s seznama — na njem so vsi dobavitelji, ki jih poznamo, zato ga zožite z vpisom v polje nad njim — nato odobrite. Če dobavitelja sploh ni na seznamu, ga najprej dodajte pod **Manufacturers** |
| *Matched to your catalogue by article number alone.* | Številka artikla se je ujemala, dobavitelj pa ni bil potrjen | Sama številka artikla ne zadošča. Pred odobritvijo potrdite dobavitelja |
| *The expiry date is not written as an expiry anywhere on the page…* | Datum je bil prebran kot datum poteka, a stran ga nikjer tako ne označuje | Preverite PDF. Morda gre za datum podpisa ali izdaje |
| *We cannot tell whether this is newer or older than the document on file.* | Eden od obeh dokumentov nima uporabnega datuma | Primerjajte oba PDF-ja sami in odločite |
| *This carries the same date as the document already on file for these items, so nothing says which one is current.* | Dva dokumenta za iste izdelke nosita isti datum izdaje, običajno dve reviziji, podpisani istega dne | Odprite oba PDF-ja. Obdržite poznejšo revizijo in drugo zavrnite; če sta res potrebna oba, povejte razvijalcu |
| *The article numbers in the document do not match this group's items.* | Številke artiklov na strani pripadajo drugi družini | Ne odobrite brez preverjanja. Morda je bil vložen k napačnim izdelkom |
| *The document lists article numbers, but we could not tie them to your catalogue.* | Najdene so bile številke artiklov, a nobena ni naša | Preverite, ali gre za izdelke, ki jih dejansko vodite |
| *The article numbers belong to more than one manufacturer.* | Številke artiklov na strani segajo čez dva dobavitelja | Nenavadno. Pred odločitvijo preberite PDF |

### Običajno zavrnite

| Zaslon pravi | Kaj to pomeni | Kaj storiti |
|---|---|---|
| *This is older than the document already on file for these items.* | Njegov datum je starejši od tistega, ki ga za te izdelke že imate | Zavrnite z razlogom **Out of date**, razen če veste, da je tisti na voljo napačen. Če se to ponavlja, je vir, iz katerega črpamo, zastarel — povejte razvijalcu |
| *The dates on this document do not look right.* | Datum je nemogoč ali močno izven razumnega razpona | Običajno slabo skeniran dokument. Ne odobrite brez branja |

---

## Besede, ki jih uporablja zaslon

Nekateri deli tega zaslona še vedno prikazujejo besede, ki jih sistem uporablja
interno. Vsi drugi zasloni povedo besedo iz tretjega stolpca,
[slovar](../glossary.sl.md#besede-na-zaslonu) pa nosi obe.

| Zaslon pravi | Pomeni | Drugod |
|---|---|---|
| **Production** | Šteje. Vidno povsod | **Published** |
| **Staged** | Čaka na človeka. Še ne šteje | **Waiting for review** |
| **Rejected** | Zavrnjeno. Ostane na voljo, ne šteje | **Rejected** |
| **Superseded** | Nadomeščeno z novejšim dokumentom. Ohranjeno, ni več aktualno | **Replaced** |
| **Filed** | Pravi dokument znanega dobavitelja, ki pa ne pokriva nobenega artikla, ki ga vodite | **On file** |
| **Match basis** (npr. *name-family*) | Kako je sistem dokument ujel z artiklom. Nekateri načini ujemanja so dovolj zanesljivi za samodejno objavo, drugi vedno potrebujejo človeka | **How it was matched** |
| **A link** | Povezava med enim dokumentom in enim artiklom. En dokument jih ima lahko več | |
| **A group** | Artikli, ki jih obravnavamo kot eno družino, ker jih en dokument običajno pokriva vse | |
| **C5**, **C17** in podobne kode | Interne številke pravil. Prezrite jih | |

---

## Kaj se zgodi po odobritvi

- Dokument se pojavi pod **Documents** in šteje v vaše številke pokritosti.
- Vsak artikel, naveden nad gumbom, ki ste ga pritisnili, ga zdaj prikazuje.
  Artikli, povezani le po šibkem ujemanju, čakajo pod **Links waiting for
  review on published documents** na svoj **Covers this item** ali **Does not**.
- Če nadomešča starejšega, je ta označen kot *nadomeščen z novejšim*. Ohranjen
  je, ne izbrisan — še vedno si ga lahko ogledate, prav tako ga je še vedno
  mogoče pokazati revizorju.
- Vsako dejstvo, ki ga je sistem prebral s strani — vrsto, datume, številko
  certifikata, številke artiklov — je shranjeno skupaj s stranjo, s katere je
  bilo prebrano. Če vas revizor vpraša, od kod izvira datum, mu lahko pokažete.

---

## Kam naprej

- [Missing documents](missing.sl.md) — druga čakalna vrsta v vašem dnevnem krogu: artikli, za
  katere je sistem iskal dokument in ga ni našel
- [Failed](failed.sl.md) — tretja
- [Manual](manual.sl.md) — isto delo, kot ga vidi operater
- [Documents](documents.sl.md) — vse, kar ste odobrili
- [Manufacturers](manufacturers.sl.md) — dobavitelja dodajte, preden nanj vežete dokument
- [Expiry](expiry.sl.md) — kaj se zgodi, ko se dokumentu bliža datum poteka
- [Slovar](../glossary.sl.md) — vse besede na enem mestu
