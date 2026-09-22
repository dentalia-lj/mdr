# Slovar

Vse besede, na katere boste naleteli, na enem mestu. Razdeljen na tri dele:

1. [Besede iz pravil](#besede-iz-pravil) — besednjak uredbe.
2. [Kaj morate dejansko imeti](#kaj-morate-dejansko-imeti) — za katere od teh
   dokumentov ste odgovorni in kateri so le dobrodošli.
3. [Besede na zaslonu](#besede-na-zaslonu) — besednjak sistema samega,
   vključno z izrazi, ki jih sistem še ne zna povedati v razumljivem jeziku.

**Stanje:** V uporabi. Preverjeno s kodo 15. 9. 2026.

---

## Besede iz pravil

### MDR

Evropska uredba o medicinskih pripomočkih, **Uredba (EU) 2017/745**. Pravilnik,
ki velja danes. Skoraj vsi dokumenti, ki jih sistem zbira, so dokumenti MDR.

### MDD

Direktiva o medicinskih pripomočkih, **Direktiva 93/42/EGS**. Stari pravilnik,
ki ga je nadomestila MDR. Nekateri pripomočki so še vedno pokriti z
dokumentacijo MDD.

Dokument MDD in dokument MDR nikoli nista obravnavana kot različici istega
dokumenta. Nova izjava MDR ne nadomesti stare izjave MDD — gre za ločeni
zgodovini, in sistem ju namerno vodi ločeno.

### Oznaka CE

Oznaka, ki jo proizvajalec namesti na pripomoček, s čimer izjavi, da ta
izpolnjuje evropska pravila. To je proizvajalčeva trditev, ne naša. Vaša
dolžnost kot distributerja je preveriti, da je oznaka prisotna.

### Izjava o skladnosti — "DoC"

Proizvajalčeva podpisana izjava, da pripomoček izpolnjuje pravila. **To je
najpomembnejši dokument.** Zanj zakon zahteva, da preverite, ali je bil
sestavljen, in prav njega sistem najbolj vztrajno išče.

Izjava običajno nima lastnega datuma poteka veljavnosti — glejte
[pravilo petih let](#pravilo-petih-let).

### Navodila za uporabo — "IFU"

Zloženka ali priročnik, ki je priložen pripomočku. Tudi to je dokument, za
katerega morate preveriti, da obstaja — z eno izjemo, opisano
[spodaj](#kdaj-navodila-za-uporabo-niso-obvezna).

### Priglašeni organ

Neodvisna organizacija, ki jo imenuje država EU in preverja delo
proizvajalca. Vsak priglašeni organ ima **štirimestno številko**.

Certifikata priglašenega organa vam ni treba hraniti. Morate pa preveriti, da
se njegova štirimestna številka pojavi na izjavi, kjer dokumentacija
nakazuje, da je bil priglašeni organ vključen. Ta omejitev izhaja iz sodbe
Sodišča Evropske unije, zadeva **C-10/24**, z dne 4. 6. 2026.

### Certifikat ES

Certifikat, ki ga priglašeni organ izda proizvajalcu. Dobro ga je imeti — je
koristno dokazilo in koristen adut pri dobavitelju — vendar **za to niste
odgovorni**. Sistem ga prikaže, a vas zaradi njega nikoli ne oceni slabše.

### ISO 13485

Proizvajalčeva registracija sistema vodenja kakovosti. Enak status kot
certifikat ES: prikazan, nikoli ocenjen.

### Systems and procedure pack statement — "SPP"

Izjava po **členu 22 MDR**, za komplete, sestavljene iz več pripomočkov z
oznako CE. Ni niti izjava o skladnosti niti certifikat, zato jo sistem vodi
kot svojo lastno vrsto dokumenta, da je nikoli ne zamenjamo z izjavo o
skladnosti.

### Razred pripomočka

Kako tvegan je pripomoček. V naraščajočem vrstnem redu: **I**, **IIa**,
**IIb**, **III**.

Razred I se deli na štiri podrazrede, in ta delitev je pomembna, ker
spreminja, kaj morate imeti:

| Razred | Kaj to pomeni |
|---|---|
| **I** | Navadni razred I. Proizvajalec skladnost izjavi sam; priglašeni organ ni vključen |
| **Is** | Razred I, dobavljen sterilno. Priglašeni organ preveri sterilnost |
| **Im** | Razred I, z merilno funkcijo. Priglašeni organ preveri merjenje |
| **Ir** | Razred I, kirurški instrument za večkratno uporabo. Priglašeni organ preveri večkratno uporabo — **in navodila, ki jih spremljajo** |

Prav zaradi te zadnje točke morajo imeti pripomočki razreda Ir vedno na voljo
navodila, medtem ko jih navadni razred I in IIa včasih ne potrebujeta.

Ta delitev izhaja iz **člena 52(7) MDR**.

### REF ali številka artikla

Proizvajalčeva lastna številka artikla, natisnjena na škatli. Sistem prebere
REF iz dokumenta, da ugotovi, katere vaše artikle ta dokument pokriva.

Pozor: vaša številka iz Business Centrala in dobaviteljeva številka za isti
artikel se navadno razlikujeta. Sistem pozna obe.

### UDI, UDI-DI in Basic UDI-DI

**UDI** je sistem enotne identifikacije pripomočkov (Unique Device
Identification) — koda, ki pripomoček identificira po vsem svetu.

- **UDI-DI** identificira en konkretni model pripomočka.
- **Basic UDI-DI** identificira družino sorodnih modelov in je koda, ki jo
  izjava običajno navaja.

Pripomočku mora biti dodeljen UDI — to je ena od vaših štirih dolžnosti.
Sistem tega trenutno ne ocenjuje, ker Business Central ne hrani nobenih
UDI-jev, trajno rdeča vrstica pa ljudi samo nauči, da kartico prezrejo.

### EUDAMED

Evropska podatkovna baza medicinskih pripomočkov. Sistem iz nje bere, da
navzkrižno preveri, kaj so proizvajalci registrirali.

### EUDAMED ID (SRN)

Identiteta proizvajalca v EUDAMED. **SRN** pomeni Single Registration Number
(enotna registracijska številka) in tako jo imenuje evropski register; zasloni
jo ob prvi omembi na strani izpišejo kot **EUDAMED ID (SRN)**. Eno ime podjetja
jih ima lahko več, zato vas sistem včasih prosi, da potrdite, katera pripada
kateremu dobavitelju.

---

## Kaj morate dejansko imeti

Dentalia je **distributer**, ne proizvajalec. Prav to dejstvo določa vse
spodaj.

**Člen 14(2) MDR** distributerju nalaga štiri stvari, ki jih mora preveriti,
preden pripomoček proda:

1. Pripomoček nosi oznako CE in je bila sestavljena EU izjava o skladnosti.
2. Pripomočku so priložene informacije po členu 10(11) — oznaka in navodila
   za uporabo.
3. Kjer je to relevantno, je uvoznik izpolnil svoje lastne obveznosti.
4. Pripomočku je bil dodeljen UDI.

To je celoten seznam. **Certifikata priglašenega organa na njem ni.**

### Za kaj vas sistem oceni slabše

Samo tri stvari:

| Vrstica | Zakaj |
|---|---|
| **Izjava o skladnosti** | Dolžnost 1. Vedno obvezna, za vsak razred |
| **Navodila za uporabo** | Dolžnost 2 |
| **Številka priglašenega organa** | Dolžnost 1, kot jo omejuje sodba C-10/24: štirimestna številka se mora pojaviti tam, kjer je bil vključen priglašeni organ |

### Kaj je prikazano, a nikoli ocenjeno

| Vrstica | Zakaj |
|---|---|
| **Certifikat ES** | Ni dolžnost distributerja. Dobro ga je imeti |
| **ISO 13485** | Ni dolžnost distributerja. Dobro ga je imeti |

Manjkajoč certifikat ES ali registracija ISO nikoli ne bo prikazana rdeče.
Kvečjemu je lahko oranžno.

### Po razredu pripomočka

| Razred | Izjava | Navodila | Številka priglašenega organa |
|---|---|---|---|
| **I** | Obvezna | Običajno obvezna | Ni relevantno |
| **Is** | Obvezna | Običajno obvezna | Obvezna |
| **Im** | Obvezna | Običajno obvezna | Obvezna |
| **Ir** | Obvezna | **Vedno obvezna** | Obvezna |
| **IIa** | Obvezna | Običajno obvezna | Obvezna |
| **IIb** | Obvezna | **Vedno obvezna** | Obvezna |
| **III** | Obvezna | **Vedno obvezna** | Obvezna |
| *razred ni znan* | Obvezna | Ni mogoče povedati | Ni mogoče povedati |

Kadar nam Business Central ne pove razreda artikla, sistem to pove namesto
ugibanja. Dolžnost glede izjave ni odvisna od razreda, zato velja v vsakem
primeru.

### Kdaj navodila za uporabo niso obvezna

**Priloga I, oddelek 23.1(d) MDR** dovoljuje, da je pripomoček razreda I ali
IIa dobavljen brez navodil, če ga je mogoče varno uporabljati brez njih in če
je proizvajalec to utemeljil v svoji tehnični dokumentaciji.

Torej "manjkajoča navodila za uporabo" pri artiklu razreda I ali IIa pomenijo
**oranžno, ne rdeče**. Pomeni: preverite, ali ta utemeljitev obstaja. Ne
pomeni, da kršite predpise.

Za Ir, IIb ali III to nikoli ne velja.

### Pravilo petih let

Izjava o skladnosti po MDR nima datuma poteka veljavnosti. To ne pomeni, da
velja večno.

Sistem šteje, da je izjavo treba znova pregledati **pet let po njeni
izdaji**. Na zaslonu to piše kot *"Due a review (5-year rule)"*. To je
opozorilo, da preverite pri dobavitelju, ne kršitev.

### "Expires soon"

**30 dni** pred datumom. To je Dentaliina lastna odločitev, sprejeta
18. 8. 2026, in namerno je to ista meja, ob kateri sistem začne poganjati
obnovitve — tako da dokument nikoli ne kaže kot v redu na dan, ko ga sistem
že aktivno preganja.

---

## Besede na zaslonu

### Stanje dokumenta

Videli jih boste kot barvne oznake.

| Oznaka | Pomeni | Shranjeno kot |
|---|---|---|
| **Published** | Šteje se. Vidno povsod | `production` |
| **Waiting for review** | Čaka na osebo. Še se ne šteje | `staged` |
| **Rejected** | Nekdo se je odločil proti njemu. Ostane v arhivu, se ne šteje | `rejected` |
| **Replaced** | Novejši dokument je zavzel njegovo mesto. Ohranjen, ni več veljaven | `superseded` |
| **On file** | Pristen dokument znanega dobavitelja, ki ne pokriva nobenega artikla, ki ga imate na zalogi. Pravilen, popoln, v nikogaršnji čakalni vrsti | `filed` |

Stolpec **Shranjeno kot** je tisto, kar hrani podatkovna baza in po čemer filtrira
zaslon [Documents](pages/documents.sl.md). Tega vam ni treba nikoli vtipkati:
filter ponudi besede in namesto vas filtrira po shranjeni vrednosti.

Nič se nikoli ne izbriše. Zavrnjen ali nadomeščen dokument ostane v arhivu, z
datumom, in ga je še vedno mogoče pokazati revizorju.

### Zasloni, kot jih imenuje meni

| Beseda | Pomeni |
|---|---|
| **Today** | Kjer pristanete. Kaj vas čaka danes zjutraj, po vrsti, kot je treba opraviti |
| **Review** | Dokumenti, ki čakajo na vašo odločitev |
| **Missing documents** | Kar je sistem iskal in ni našel. Pisarniški seznam slepih ulic |
| **Expiring** | Dokumenti, katerih datum je blizu ali že mimo |
| **Renewal emails** | Zahtevki za obnovitev, ki jih je sistem napisal, da jih pošljete |
| **Items** | Vaši artikli, kot jih oštevilči Business Central |
| **Documents** | Vse, kar je v arhivu, ne glede na stanje |
| **Manufacturers** | Vaši dobavitelji, vsak po en vnos |
| **EUDAMED checks** | Kaj evropski register pove o vseh dobaviteljih, na enem zaslonu |
| **Emails received** | Sporočila, ki jih je sistem prebral, in kaj je našel priloženo |
| **System status** | Operaterska plošča: kako gre stroju samemu. Do 14. septembra 2026 se je glasila "Queue status" |

### Druge vsakdanje besede

| Beseda | Pomeni |
|---|---|
| **Item** | En artikel, kot ga hrani Business Central. Njegova številka je označena z **Item no.** |
| **Manufacturer** | En dobavitelj kot ena sama entiteta, ne glede na to, koliko imen ima zanj Business Central |
| **Name in Business Central** | Ime, ki ga Business Central uporablja za tega dobavitelja. Več jih lahko kaže na enega proizvajalca |
| **Manufacturer's article no.** | Dobaviteljeva lastna številka artikla, ki navadno ni enaka vaši |
| **Povezava** | Zveza med enim dokumentom in enim artiklom. En dokument jih ima lahko več |
| **Skupina** | Artikli, obravnavani kot ena družina, ker jih navadno vse pokriva en sam dokument |
| **Pokritost** | *skupina artiklov* pomeni, da pokriva poimenovano družino; *vse od tega proizvajalca* pomeni, da pokriva celoten program dobavitelja |
| **How it was matched** | Zakaj je sistem dokument povezal s tem artiklom. Nekaterim načinom sistem dovolj zaupa, da objavi samodejno; drugi vedno potrebujejo osebo |
| **Dokazila** | Za vsako dejstvo, ki ga je sistem shranil: stran, s katere ga je prebral, in točne besede. To pokažete revizorju |
| **Playbook** | Kar sistem ve o enem dobavitelju: njegovo spletno stran, kje živijo njegovi dokumenti, kako je urejena njegova dokumentacija |
| **Short name** | Kratko ime z malimi črkami in vezaji, pod katerim je playbook shranjen, na primer `ivoclar`. Izbrano enkrat in nato pri miru |
| **EUDAMED check** | Preverjanje enega proizvajalca proti EUDAMED. Nikoli ne steče samo od sebe |
| **Start the check** | Gumb, ki ga požene. Vedno ga pritisne oseba |
| **Check due** | Seznam dobaviteljev, pri katerih lahko preverjanje zaženete zdaj |

### Besede o tem, kako je dokument povezan z artiklom

To so vrednosti pod stolpcem **How it was matched** in videli jih boste v
drobnem tisku. Pomembno razlikovanje: nekateri sistemu dovolijo, da objavi sam,
drugi vedno počakajo na osebo.

| How it was matched | Se objavi samodejno? |
|---|---|
| `ref-list`, `ref-item` | Da — dobaviteljeva ali vaša lastna številka artikla, pri čemer je proizvajalec že potrjen |
| `udi`, `basic-udi-di` | Da |
| `mfr-scope` | Da — dokument pokriva celoten program dobavitelja |
| `manual` | Da — odločila je oseba |
| `name-family`, `fetch-context`, `ref-catalogue` | **Nikoli.** Vedno počaka na osebo |

### Besede o delu sistema samega

Te se pojavljajo na System status, Processing, Manual in Failed. Opisujejo
stroj, ne vaših dokumentov. Vsi štirje so operaterski zasloni in nekateri med
njimi še nosijo starejši zapis besede; kjer je tako, je spodaj naveden, da ga
prepoznate.

| Beseda | Pomeni |
|---|---|
| **Being read** | Sistem ima datoteko in jo predeluje. Zaslon, ki jih našteva, je še vedno naslovljen **Processing**, njegovo edino število pa se še vedno glasi **documents in flight** |
| **to review** | Čaka na vašo odločitev. To je zaslon Review |
| **Failed** | Opravilo, ki je večkrat spodletelo in obupalo. Nič ni izgubljeno; lahko ga ponovno poženete. Na System status je isto našteto pod ploščico **dead jobs**, ki šteje čisto vsa, tudi tista, ki jih je kdo že poslal nazaj |
| **pending / running / done / failed** | Faze enega koščka lastnega dela sistema |
| **Check due** | Dobavitelj je na vrsti za preverjanje v EUDAMED. Čaka, da ga zaženete. Na System status je ploščica zanj še vedno označena **sweep due** |
| **ok / due / stale / never** | Ali se je načrtovano opravilo izvedlo dovolj nedavno |
| **Manual** | Operaterjev lastni seznam odprtih opravil, zložen pod Queues & health. Pisarna tisti del, ki terja ukrepanje, bere kot **Missing documents** |

### Besede o e-pošti

| Beseda | Pomeni |
|---|---|
| **Emails received** | Sporočila, ki jih je sistem prebral, in kaj je našel priloženo |
| **Renewal emails** | Zahtevki za obnovitev, ki jih je sistem napisal, da jih pošljete |
| **Draft / Ready to send / Sent / Archived** | Faze enega od teh osnutkov. Vsak zaslon, ki katero od njih poimenuje — Renewal emails, sam osnutek, stolpec **Chase** na Expiry in **Renewal chase** na dokumentu — uporablja te štiri besede |

**Sistem nikoli ne pošlje e-pošte.** Napiše sporočilo; oseba ga pošlje in
nato označi kot *sent*.

### Kode, ki jih lahko prezrete

`C5`, `C12`, `C17`, `G3`, `T0`, `T1`, `T2`, `T3`, `ref-list`, `name-family`,
številke migracij, imena opravil s piko, kot je `gate.apply`.

To so interne oznake. Na zaslonu so, ker so bili nekateri deli sistema
zgrajeni najprej za razvijalce. Nikoli ne spremenijo tega, kaj morate
storiti. Če vam zaslon pokaže eno od njih in nič drugega ni smiselno, je to
napaka, vredna prijave, ne nekaj, kar bi morali razvozlati.

---

## Kam naprej

- [Začnite tukaj](00-getting-started.sl.md) — prijava in kako se znajti
- [Vaš dnevni krog](01-daily-work.sl.md) — kaj storiti vsako jutro
- [Review](pages/review.sl.md) — zaslon, ki ga boste uporabljali največ
