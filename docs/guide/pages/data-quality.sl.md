# Data quality

**V enem stavku:** tri ločene table — ena navaja imena proizvajalcev,
natisnjena na čakajočih dokumentih, ki jih sistem še ne pozna, druga
nenavadnosti v vašem seznamu izdelkov, tretja pa primerja razred pripomočka po
Business Centralu s tem, kar dejansko navajajo vaši dokumenti.

**Stanje:** V živo. Preverjeno na delujoči strani 31. 8. 2026; prva tabla
dodana 29. 9. 2026.

---

## Kaj je to

Ta stran je samo za branje. Tu ne zapiše ničesar, niti kjerkoli drugje —
Business Central ostaja edini verodostojni vir.

Prva tabla, **Printed names without a manufacturer**, navaja dokumente, ki
čakajo na pregledu, ker ime proizvajalca, ki ga natisnejo, sistemu ni znano.
Dobavitelj pogosto natisne svoje polno pravno ime (na primer "botiss
biomaterials GmbH"), Business Central pa ima kratko oznako ("BOTISS"). Dokler
tega imena ne dodamo v dobaviteljev playbook, sistem ne ve, čigav je dokument,
zato tudi številk artiklov ne more preveriti. Tabla dokumente združi po
natisnjenem imenu in predlaga proizvajalca, skupaj z dokazom za predlog.

Druga tabla je tekoč seznam stvari, ki jih sistem ni znal razumeti pri branju
vašega seznama izdelkov iz Business Centrala. Večina tega je vprašanje za
tistega, ki upravlja ta izvoz, ne za vas.

Tretja tabla je drugačna in je bolj pomembna za vas: postavi razred pripomočka
po Business Centralu za posamezen izdelek ob razred, ki ga dejansko navajajo
dokumenti, ki jih hranite, tako da ju je mogoče primerjati.

---

## Kdaj to uporabite

- Tedensko, kadar tedensko poročilo pove, da imena čakajo: na prvi tabli
  vidite, katera in zakaj.
- Mesečno, ali kadar vas razvijalec ali vaš kontakt za Business Central prosi,
  da si tu nekaj ogledate.
- Kadar želite razred pripomočka izdelka izpolniti na podlagi dokumentacije,
  ki jo že imate, namesto da bi ugibali.
- Kadar želite preveriti, ali se zabeleženi razred izdelka dejansko ujema z
  njegovo izjavo o skladnosti.

---

## Preden začnete

Nič. Dovolj je, da ste prijavljeni.

---

## Kaj storite

1. Kliknite **Data quality** v bloku **Operator** na dnu menija.
2. Na tabli **Printed names without a manufacturer** preberite vsako vrstico:
   ime, koliko dokumentov čaka nanj (kliknite število, da jih izpišete),
   predlaganega proizvajalca in **What to do**. Ime in predlog posredujte
   razvijalcu, ki ga doda v playbook. Nič na tej tabli samo ničesar ne
   spremeni.
3. Na tabli **By kind** kliknite vrsto, da spodnji seznam filtrirate samo na
   to vrsto.
4. Na tabli razreda pripomočka preberite razpredelnico **To look at**. Vsaka vrstica je en
   izdelek, katerega razred po Business Centralu je treba pregledati.
5. Odprite izdelek ali dokument iz vrstice, odločite, kaj je pravilno, in
   popravek posredujte tistemu, ki vzdržuje Business Central. Ta stran
   spremembe ne more izvesti sama.

---

## Kaj se zgodi nato

### Tabla 1 — natisnjena imena brez proizvajalca

Ko razvijalec ime doda v playbook in čakajoče dokumente ponovno preveri, se
vsak znova ujema. Dokument, katerega številke artiklov se ujemajo z vašimi
izdelki, se objavi sam, njegova naloga na pregledu pa se zapre. Dokument,
katerega številke se ne ujemajo z ničemer, kar imate na zalogi, ostane na
pregledu, zdaj z natančnejšim razlogom. Prihodnji dokumenti s tem imenom se
ujemajo, ne da bi kdorkoli karkoli storil.

### Tabla 2 — nenavadnosti v vašem seznamu izdelkov

Vsaka vrstica je nekaj, kar je sistem prebral iz Business Centrala in ni znal
razložiti. Ponovljena nenavadnost poveča števec, namesto da bi bila znova
navedena, zato ostane ta seznam velik toliko kot problem sam, ne toliko kot
vaš katalog.

### Tabla 3 — razred pripomočka proti Business Centralu

Vsaka vrstica primerja razred izdelka po Business Centralu s tem, kar
dejansko navaja dokument, ki ga hranite za ta izdelek. Nič od tega ne
spremeni Business Centrala. Samo pove vam, kje se oba ujemata, kje je eden
prazen in kje se dejansko razhajata.

---

## Kaj lahko gre narobe

### Tabla 1 — kaj pomeni posamezen predlog

| Kar vidite | Kar to pomeni | Kaj storiti |
|---|---|---|
| **Alias exists** | Ime je že znano; ti dokumenti so bili preverjeni, preden je bilo dodano | Prosite razvijalca, da jih ponovno preveri (en ukaz) |
| **Article numbers** | Večina številk artiklov na dokumentih pripada izdelkom enega proizvajalca: močan dokaz | Ime in predlagani playbook posredujte razvijalcu |
| **Name only** | Ime vsebuje blagovno znamko iz Business Centrala, a nobena številka artikla tega ne potrdi | Odprite en dokument in preverite proizvajalca, preden predlog posredujete |
| **No match** | Niti številke artiklov niti ime ne kažejo na nikogar | Oglejte si dokumente. Morda gre za distributerja, čigar ime nikoli ne sme postati ime proizvajalca |

### Tabla 2 — kaj pomeni posamezna vrsta

Stolpec **Kind** prikaže eno od teh internih imen. To je edino mesto na tej
strani, kjer se surovemu imenu ni mogoče izogniti — zanj ni oznake ali
navadne besede, ki bi ga nadomestila.

| Kar vidite | Kar to pomeni | Kaj storiti | Čigav problem |
|---|---|---|---|
| `md_class_blank` | Business Central nima zabeleženega razreda tveganja za ta izdelek | Vredno izpostaviti, če opazite vzorec | Podatek Business Centrala |
| `mfr_ref_missing` | Dobaviteljeva lastna številka artikla za ta izdelek ni na voljo | Nič za preganjati — to je opisno, ne cilj | Informativno |
| `mfr_ref_prose` | V polju, kjer bi morala biti dobaviteljeva številka artikla, je namesto tega stavek | Vredno izpostaviti tistemu, ki vnaša podatke v Business Central | Podatek Business Centrala |
| `manufacturer_code_blank` | Business Central nima zabeležene dobaviteljeve kode za ta izdelek | Vredno izpostaviti | Podatek Business Centrala |
| `reclassified_non_md` | Sistem je ta izdelek nekoč vodil kot pripomoček, Business Central pa je nato povedal, da to ni | Informativno, razen če to spremeni, kaj pričakujete videti na Status | Informativno |
| `cert_reference_unresolved` | Izjava o skladnosti omenja certifikat, ki ga sistem nima | Če ga potrebujete, certifikat izterjajte pri dobavitelju | Vaše, da izterjate |
| `no_text_layer` | Dokument je slika brez besedila, ki bi ga sistem lahko izluščil | Če se to pri istem dobavitelju ponavlja, ga prosite za pravo datoteko | Razvijalčevo, razen če gre vedno za istega dobavitelja |
| `basic_udi_check_failed` | Koda identifikatorja pripomočka na dokumentu ni prestala lastnega internega preverjanja | Razvijalčevo | Razvijalčevo |
| `t0_ref_template_miss` | Pravila za branje dokumentacije enega dobavitelja so prenehala delovati, ker je dobavitelj spremenil videz svoje strani | Razvijalčevo — tu ni ničesar, kar bi lahko popravili sami | Razvijalčevo |

### Tabla 3 — kaj pomeni posamezna ocena

| Kar vidite | Kar to pomeni | Kaj storiti | Dobro ali slabo |
|---|---|---|---|
| **agree** | Razred po Business Centralu in razred po dokumentu se ujemata | Nič. Se šteje, ne navaja | Dobro |
| **agree-family** | Business Central je zabeležil bolj natančen razred — sterilno, merilno ali kirurško za večkratno uporabo — dokument pa navaja le navaden, splošni razred. Izjave skoraj nikoli ne navedejo te podrobnosti, zato to ni pravo razhajanje | Nič. Se šteje, ne navaja | Dobro |
| **fillable** | Business Central nima zabeleženega razreda, dokument, ki ga hranite, pa ga navaja | Razred posredujte tistemu, ki vzdržuje Business Central, in navedite dokument, iz katerega izhaja | Priložnost, ne problem |
| **conflict** | Razred po Business Centralu in razred po dokumentu se dejansko razhajata | Odprite dokument in preverite, ali je vpisan na pravi izdelek. Če je, je to vredno izpostaviti — bodisi zapis v Business Centralu bodisi dokument potrebuje ponoven pregled | Vredno raziskati |

Po izdelkih so navedene samo vrstice **fillable** in **conflict**, ker sta
edini, pri katerih je treba kaj storiti. **agree** in **agree-family** se
štejeta v povzetku nad seznamom in nič več.

---

## Besede, ki jih uporablja zaslon
| Kar piše na strani | Kar to pomeni |
|---|---|
| **Printed name** | Ime proizvajalca natanko tako, kot ga natisne dokument |
| **Waiting** | Koliko dokumentov na pregledu natisne to ime |
| **Kind** | Katera od devetih znanih nenavadnosti je posamezna vrstica |
| **Subjects** / **Observations** | Koliko različnih izdelkov ali dokumentov je naletelo na to nenavadnost in kolikokrat je bila skupno opažena |
| `data_anomaly`, `item_class_check`, migration numbers | Interna imena razpredelnic in datotek, vidna, če odprete majhno puščico ob povzetkovni vrstici strani. Ne upoštevajte jih |
| **BC class** / **Document states** | Zabeleženi razred po Business Centralu in razred, ki ga navaja dokument, ki ga hranite |
| **Verdict** | Eno od `agree`, `agree-family`, `fillable`, `conflict` — glejte razpredelnico zgoraj |

---

## Sorodno
- [System status](status.sl.md) — **missing mfr_ref** se pojavi tudi tam, kot ena od
  glavnih ploščic
- [Items](items.sl.md) — odprite posamezen izdelek za ogled celotnega zapisa
- [Documents](documents.sl.md) — odprite posamezen dokument za ogled, kaj
  navaja
- [Glossary](../glossary.sl.md) — razred pripomočka in zakaj se razred I deli
  na štiri
