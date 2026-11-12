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
| **Links waiting on documents that were never published** | Nič. Ta seznam je zgolj v vednost. Ni gumba in ni ničesar za odločiti. Če nadomeščen dokument nekdo ponovno odpre in znova odobri, se z njim objavijo njegove povezave, ujete na zanesljiv način |

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
5. Nad gumbi plošča pove, kaj bo odobritev naredila: *Approving publishes it
   for these N items. It answers for an item only where it is the current
   one of its type (MDR first, then the newest date).*, sledijo pa številka in ime vsakega artikla (prvih deset;
   za ostale pritisnite **+ K more**) in kako so bili ujeti. Artikel, ki že ima
   novejši dokument iste vrste, obdrži tega; ta dokument ostane kot njegova
   zgodovina. Pri dokumentu, ki pokriva celoten program dobavitelja, piše
   *Approving makes it count for all N ⟨dobavitelj⟩ items*, oziroma *…for the
   one ⟨dobavitelj⟩ item*, kadar ima dobavitelj en sam artikel s pripomočkom.
6. Primerjajte PDF z dejstvi ob njem: vrsto dokumenta, datume, artikle.
7. Pritisnite en gumb:

| Gumb | Kaj naredi |
|---|---|
| **Approve for these N items** (**Approve for this item**, kadar je artikel en sam) | Dokument od zdaj šteje, in sicer natanko za artikle, navedene nad gumbom. Pojavi se pod Documents, vsak od teh artiklov pa ga zdaj prikazuje |
| **Approve** | Enako, le da odobritev še ne seže do nobenega artikla. Plošča pove, zakaj: dokumenta ne povezuje z artiklom nič, ali pa je povezan le po šibkem ujemanju (glejte spodaj) |
| **Reject…** | Najprej vpraša, zakaj. Izberite enega od šestih razlogov spodaj, po potrebi dodajte opombo, nato pritisnite **Reject**. Dokument ne šteje. Ostane na voljo, označen kot zavrnjen, in ga je mogoče znova odpreti. Vaše ime in razlog se zabeležita, razlog pa je viden na strani [Decisions](decisions.sl.md). Sistem bo naprej iskal boljšega. **Cancel** zapre razloge in ne spremeni ničesar |
| **Correct a fact first** | Odpre polja za pravila, datume in številko certifikata. Vrste dokumenta tukaj ni: spremenite jo s **Change type...**, ki najprej pokaže, kaj sprememba naredi. Spremenite le tisto, kar je na dokumentu napačno; popravki se shranijo, ko odobrite. Dokument brez datuma izdaje kaže "Not stated. Enter it before approving." in ga ni mogoče odobriti, dokler datuma ne vnesete tukaj. Datum, ki ga v koledarju ni, na primer 31.04, se zavrne, preden se karkoli pošlje: *The Issued date is not a real date (31.04 is not).* Popravite ga in znova odobrite. Pri dokumentu za celoten program ponuja samo datum izdaje |
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

### Izbira artiklov, ki jih dokument pokriva

Nekateri dokumenti, pogosto navodila za uporabo, ne navajajo kataloških številk,
zato jih nič ne poveže z artiklom. Odprite vrstico in pritisnite **Find items
this document covers…**.

- Proizvajalec je zaklenjen na proizvajalca dokumenta. Če ta ni potrjen, ga
  najprej izberite; najti je mogoče samo njegove artikle. Kjer se ime v
  Business Centralu razlikuje od uradnega, ga seznam prikaže poleg njega.
- Iskanje se začne z besedo iz imena datoteke. Vpišite številko artikla ali
  besedo iz imena. Številka prikaže artikle, katerih številka jo vsebuje, pod
  njimi pa 10 številk pred njo in 10 za njo, ki so pogosto iz iste družine:
  preverite vsako. Beseda najde tudi podobno zapisane ("varibase", "emax" za
  E.MAX). Prikaže največ 200 artiklov; če jih ustreza več, uporabite ožjo
  besedo.
- **Similar** v katerikoli vrstici prikaže artikle istega proizvajalca s
  podobnim imenom. Objavljen dokument istega proizvajalca, ponujen pod iskalnim
  poljem, prikaže artikle, ki jih pokriva.
- Artikle označite enega za drugim ali z **Tick all** v katalogni skupini.
  Ničesar ne označimo namesto vas. Artikel, ki je bil za ta dokument že
  zavrnjen, ima oznako **Refused earlier**. Namenoma ga lahko označite
  posamično, s čimer ob odobritvi razveljavite zavrnitev. **Tick all** ga
  preskoči.
- Vsaka vrstica pokaže, ali ga Business Central vodi kot medicinski pripomoček,
  katere dokumente že ima (DoC, CE, IFU, ISO) in njegov najnovejši dokument te
  vrste z datumom.
- **Done** zapre izbirnik, plošča pa pokaže, za katere artikle bo dokument
  veljal po odobritvi. **Cancel** ohrani prejšnjo izbiro.

Dokler ne odobrite, ni povezano nič. Nato vsak označeni artikel postane potrjena
povezava pod vašim imenom. Artikel, ki že ima dokument iste vrste, ga obdrži;
novi se doda zraven.

### Odstranitev napačnega artikla

Na seznamu *Approving publishes it for…* ima vsak artikel, ki ga je našel
sistem, gumb **Remove**. Artikel se premakne pod *Will not count*, z **Undo** ga
vrnete. Dokler ne odobrite, se nič ne shrani. Ob odobritvi se vsak zabeleži kot
nepokrit in ponovno branje dokumenta ga ne bo več povezalo. Uporabite ga, kadar
dokument navaja številko drugega izdelka, na primer v tabeli za naročanje.

### Sprememba vrste dokumenta

Včasih je sistem dokument napačno prebral: izjavo, na primer, je vpisal kot
certifikat. Pod dejstvi na desni, nad gumbi, pritisnite **Change type...** (majhen
obrobljen gumb v svoji vrstici). Na voljo je pri vsakem dokumentu, ki čaka na
Review, kratka vrstica ob njem pa pove, kdaj ga uporabiti.

1. Odpre se majhen obrazec, izpolnjen s tem, kar dokument zdaj je: **Type**,
   **Rules** in **What it covers** (*specific items* ali *whole range*).
   Spremenite, kar je narobe. Ko spremenite **Type**, se **What it covers**
   premakne na to, kar ta vrsta običajno pokriva: izjave in navodila pokrivajo
   določene artikle, certifikati (EC, ISO) celoten program. Lahko ga še vedno
   vrnete nazaj.
2. Pritisnite **Check what changes**. Še nič se ne shrani. Pod obrazcem zaslon
   našteje vsako posledico, in to samo tiste, ki veljajo:
   - vrsto, pravila in kaj dokument pokriva, vsako kot *prej → potem* ali
     *(unchanged)*;
   - *N items now linked will be taken off: …* (prvih deset je poimenovanih);
   - izjave, ki ta dokument navajajo kot svoj certifikat in ga ne bodo več;
   - pri certifikatu izjave, ki bodo nanj kazale, ali *No declaration can point
     to it until a certificate number is entered.*;
   - kaj se zgodi s številko certifikata in datumom izteka. Ostaneta, kakršna
     sta; če sta napačna, ju popravite pod **Correct a fact first**, ko
     odobrite;
   - zadnja vrstica: *It stays on Review. Nothing is published. You approve it
     next, in the specific items panel.* (ali *whole range*).
3. Obkljukajte polje *I have checked the document: it is a ⟨vrsta⟩, for ⟨kaj
   pokriva⟩.* Gumb **Yes, change the type** ostane siv, dokler tega ne storite.
   **Cancel** ne spremeni ničesar.
4. Pritisnite **Yes, change the type**. Vrstica se zamenja z *The type change
   for this document is recorded. It comes back on Review in the specific items
   panel within a minute, and you approve it there. Nothing is published.*

Dokument ne zapusti Review in zaradi spremembe nič ne šteje. Čez minuto je
nazaj, v plošči, ki ustreza temu, kar zdaj pokriva, v njegovem razlogu pa piše
*Type changed by ⟨vaše ime⟩ on ⟨datum⟩.* Nato odločite kot običajno: odobrite ali
zavrnite. Vsaka sprememba vrste se shrani z vašim imenom in časom. Ponovno
branje dokumenta pozneje nikoli ne spremeni tistega, kar ste nastavili pri
**What it covers**, in mu nikoli ne poveže artiklov. Dokumentu, ki pokriva
določene artikle, jih dodajte sami: tukaj z **Find items this document
covers…**, ko je objavljen, pa pod **Documents** z **Add items this document
covers…**. Dokument za celoten program, ki ste mu spremenili vrsto, velja za
artikle, za katere je bil odobren, in za nobene druge: artikel, ki pride v
katalog pozneje, se mu ne pridruži, ročno pa mu artiklov ni mogoče dodati.
Povejte razvijalcu, kadar mora tak certifikat pokriti nov artikel.

**Objavljen dokument.** **Change type...** se pojavi samo pri dokumentih, ki
čakajo na Review. Objavljen ali nadomeščen dokument mora najprej stran: zavrnite
ga, nato ga pod **Documents** znova odprite z **Reopen this document**. Vrne se
na Review, tam pa mu lahko spremenite vrsto.

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
| *The document does not say which items it covers.* | Nič na strani ne navaja številke artikla | Zelo pogosto pri izjavah za celoten program. Uporabite **Approve for all N items** in izberite dobavitelja |
| *This document names a certificate we do not have on file yet.* | Sklicuje se na certifikat, ki ga še nimamo | Če je dokument sicer v redu, ga odobrite. Manjkajoči certifikat poiščite ločeno |
| *This does not look like a medical device document, so it was held back from automatic filing.* | Ni navedbe MDR ali MDD in ni certifikata kakovosti — lahko gre za izjavo o strojih, kozmetiki ali elektriki | Odprite PDF. Če dokument res govori o pripomočku, ga odobrite; sicer ga arhivirajte, da se tu ne pojavlja več |
| *The article numbers point at more than one group of items.* | En dokument pokriva izdelke, ki jih vodimo v ločenih družinah | Odobrite, če resnično pokriva vse |

### Najprej natančno preglejte PDF

| Zaslon pravi | Kaj to pomeni | Kaj storiti |
|---|---|---|
| *We could not confirm which manufacturer issued this.* | Ime na dokumentu se ne ujema z ničimer v Business Centralu | Običajno pri novem dobavitelju. Izberite pravega s seznama — na njem so vsi dobavitelji, ki jih poznamo, zato ga zožite z vpisom v polje nad njim — nato odobrite. Seznam navaja dobavitelja z njegovim uradnim imenom; kjer ga Business Central imenuje drugače, sledi še to ime, npr. *Chemichl AG · BC: JENSEN, MIYO*, in vpis katerega koli od obeh imen ga najde. Če dobavitelja sploh ni na seznamu, ga najprej dodajte pod **Manufacturers** |
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
| *Older than the document already on file for every item it names.* | Ta dokument je starejša različica tega, kar je že v evidenci. Odobritev ga ohrani kot zgodovino; za noben izdelek ne nadomesti veljavnega | Zavrnite ga, če je le star izvod |
| *The dates on this document do not look right.* | Datum je nemogoč ali močno izven razumnega razpona | Običajno slabo skeniran dokument. Ne odobrite brez branja. Natisnjen dan, ki presega konec meseca (*April 31st, 2020*), se ne označi: zabeleži se zadnji dan meseca, 30. april 2020, dokaz pa ohrani natisnjeno |

### Kadar gumb zavrne

Zavrnitev pomeni, da se ni zgodilo nič. Vsaka se konča z *Nothing was changed.*

| Zaslon pravi | Kaj to pomeni | Kaj storiti |
|---|---|---|
| *A reading of this document is in progress, or another decision on it is waiting to apply. Try again later.* | Sistem dokument ravno bere znova ali pa odločitev o njem še ni uveljavljena | Počakajte minuto in pritisnite znova |
| *This document changed after you checked it. Reload the page and check again.* | Nekdo ali sistem je dokument spremenil, potem ko ga je **Check what changes** prikazal s seznamom | Ponovno naložite stran, znova pritisnite **Change type...** in preberite nov seznam |
| *This document is no longer on Review. Reload the page to see where it stands.* | Dokument je bil odločen ali premaknjen, medtem ko je bil vaš zaslon odprt | Ponovno naložite stran |
| *Nothing to change.* | Vrsta, pravila in kaj dokument pokriva so že taki, kot so izbrani | Spremenite enega od njih ali pritisnite **Cancel** |
| *This document is part of a chain of versions, so its type cannot be changed here.* | Dokument je nadomestil drug dokument ali ga je ta nadomestil | Sporočite razvijalcu |
| *This document already counts for an item, so its type cannot be changed here. Ask the person who looks after the system.* | Povezava je že objavljena | Sporočite razvijalcu |
| *Tick the box to confirm.* | **Yes, change the type** je bil pritisnjen brez kljukice | Obkljukajte polje in znova pritisnite gumb |
| *A change of type is waiting for this document. Wait for it to apply, then decide.* | Pritisnili ste **Approve** ali **Reject** ali odobritev za vse artikle, medtem ko se potrjena sprememba vrste še uveljavlja | Počakajte, da se vrstica vrne na Review (v minuti), nato odločite |
| *This document names specific items, so it cannot be approved for a whole manufacturer. Reload the page to see where it stands.* | Pritisnili ste **Approve for all N items** pri dokumentu, ki pokriva določene artikle, običajno zato, ker se je njegova vrsta spremenila po odprtju strani | Ponovno naložite stran. Uporabite **Approve for these N items** |

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
| **Superseded** | Vloženo za novejši dokument. Ohranjeno, za izdelek pa šteje le tam, kjer ga ne pokriva noben novejši dokument iste vrste | **Replaced** |
| **Filed** | Pravi dokument znanega dobavitelja, ki pa ne pokriva nobenega artikla, ki ga vodite | **On file** |
| **Match basis** (npr. *name-family*) | Kako je sistem dokument ujel z artiklom. Nekateri načini ujemanja so dovolj zanesljivi za samodejno objavo, drugi vedno potrebujejo človeka | **How it was matched** |
| **A link** | Povezava med enim dokumentom in enim artiklom. En dokument jih ima lahko več | |
| **A group** | Artikli, ki jih obravnavamo kot eno družino, ker jih en dokument običajno pokriva vse | |
| **C5**, **C17** in podobne kode | Interne številke pravil. Prezrite jih | |
| **Confidence**, pod **Technical details** | Koliko je bil program, ki je prebral stran, po lastni oceni prepričan; ta ocena ni umerjena na dokumentih, ki jih je preveril človek. Kjer sta se dve ločeni branji ujemali glede vrednosti in je bilo vsako prepričano vsaj 0,70, združuje oceni obeh branj. Za predpis je 1,0, kadar natisnjeni sklic na predpis dokazuje vrednost | |

---

## Kaj se zgodi po odobritvi

- Dokument se pojavi pod **Documents** in šteje v vaše številke pokritosti.
- Vsak artikel, naveden nad gumbom, ki ste ga pritisnili, ga zdaj prikazuje.
  Artikli, povezani le po šibkem ujemanju, čakajo pod **Links waiting for
  review on published documents** na svoj **Covers this item** ali **Does not**.
- Odobritev nikoli ne umakne drugega dokumenta. Vsak izdelek prikaže dokument,
  ki velja zanj: pri vsaki vrsti dokumenta zmaga najnovejši, dokument po MDR pa
  ima prednost pred dokumentom po MDD. Starejši dokument ostane pri izdelku kot
  zgodovina. Ohranjen je, ne izbrisan, zato si ga lahko ogledate, prav tako
  ga je še vedno mogoče pokazati revizorju.
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
