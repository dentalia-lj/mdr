# System status

**V enem stavku:** to je nadzorna plošča stroja samega — večina številk na njej
ne opisuje vaših dokumentov, temveč sistem.

**Stanje:** V živo. Preverjeno v kodi 14. 9. 2026.

---

## Kaj je to

Ta stran je samo za branje. Karkoli naredite tukaj, ne spremeni ničesar.

To je bila stran, na katero ste prišli ob prijavi. Od 14. septembra 2026 je to
[Today](today.sl.md), ta plošča pa se je preselila na svoj naslov pod **System
status**, v blok **Operator** na dnu menija. Na vrhu en stavek o izjavah, pod
njim značka za vsak del sistema, sedem številk in štirje zavihki, za vsakim še
več podrobnosti.

Le nekaj od sedmih številk je takih, na katere lahko vplivate. Ostale opisujejo,
kako se odreza stroj, in so tu predvsem zato, da jih lahko vidi razvijalec.

---

## Kdaj to uporabite

- Ko vas nekdo vpraša "koliko nas je to stalo".
- Ko razvijalec prosi, da mu preberete kakšno številko.
- Vprašanja o skladnosti so na [Today](today.sl.md), ki nosi isti stavek o
  izjavah.

---

## Preden začnete

Nič. Dovolj je, da ste prijavljeni.

---

## Kaj storite

1. Kliknite **System status** v bloku **Operator** na dnu menija.
2. Preberite stavek o izjavah na vrhu. Njegova povezava odpre artikle, ki
   nimajo izjave. Isti stavek je na [Today](today.sl.md).
3. Poglejte značke pod naslovom: ena na del sistema, z zadnjim utripom v
   namigu. Vrstica o zdravju v meniju je krajša različica istega branja.
4. Poglejte sedem ploščic. Če ploščica prikazuje številko, na katero lahko
   vplivate, jo kliknite — odpelje vas naravnost na ustrezno stran.
5. Kliknite **Coverage**, **Queue**, **Spend** ali **Recent jobs** pod
   ploščicami za več podrobnosti. **Coverage** je tisti, ki je vreden vašega
   časa; ostali trije opisujejo stroj.

---

## Kaj se zgodi nato

Najprej stavek **Declarations on file for X of Y medical-device items (Z%)**
(izjave imamo za X od Y artiklov, ki so medicinski pripomočki). To je številka,
ki jo navedete Dentalii, in je **vaša**:

- **Y** so vsi artikli, ki jih Business Central označuje kot medicinske
  pripomočke. Artikli brez razreda pripomočka v Business Centralu niso šteti,
  vrstica pod stavkom pa pove, koliko jih je.
- **X** so tisti med njimi, ki imajo objavljeno izjavo o skladnosti, povezano
  s samim artiklom: prek naše številke artikla, dobaviteljeve številke artikla,
  UDI ali dobaviteljevega seznama pokritosti. Izjava, ki pokriva celoten
  program proizvajalca, tu ne šteje, ker ne pove, katere artikle ima v mislih.
  Potekla izjava še vedno šteje kot izjava, ki jo imamo.
- Povezava **Show the N without a declaration** odpre [Coverage
  gaps](coverage.sl.md). Število **No Declaration of Conformity** tam je vedno
  isti N.

Ta stavek je nadomestil ploščico, ki je kazala 99,7 %. Ta je štela katerikoli
objavljen dokument, tudi dobaviteljev certifikat sistema kakovosti, ki o
posameznem izdelku ne pove ničesar.

Nato sedem ploščic, v vrstnem redu, kot se pojavijo:

| Ploščica | Kar prikazuje | Čigav problem |
|---|---|---|
| **missing mfr_ref** | Izdelki, pri katerih dobaviteljeva lastna številka artikla ni na voljo. Vredno vedeti, ne pa cilj, h kateremu stremite | **Vaš**, nizka prioriteta |
| **to review** | Enako število kot **Review** v meniju. Dokumenti, ki čakajo na vašo odločitev | **Vaš** |
| **manual queue** | Stvari, pri katerih sistem prosi človeka, naj jih obravnava. Pisarniška polovica tega je [Missing documents](missing.sl.md) | **Vaš**, večinoma |
| **dead jobs** | Spodletela opravila, ki še potrebujejo koga. Opravilo, ki ga je kdo že poslal nazaj, ni šteto, zato ta ploščica in [Failed tasks](failed.sl.md) kažeta isto število | **Razvijalčev** — glejte spodaj |
| **sweep due** | Dobavitelji, pri katerih je na vrsti preverjanje EUDAMED. Nič se ne zažene, dokler ne pritisnete **Start the check** | **Vaš**, občasno |
| **Documents being read** | Koliko dokumentov sistem trenutno bere | Informativno |
| **the euro amount, and "30d of €…"** | Kaj je sistem porabil v zadnjih 30 dneh, glede na mesečno omejitev | Vredno spremljati, redko nujno |

**Documents being read** šteje isto kot stran
[Processing](processing.sl.md): en dokument enkrat, ne glede na to, skozi koliko
korakov gre. Do 11. septembra 2026 se je
ta ploščica imenovala **in flight** in je štela vsako čakajoče opravilo
katerekoli vrste, tudi deset ponavljajočih se časovnikov, ki vedno čakajo.

Pod ploščicami so štirje zavihki:

- **Coverage**: štirje starejši načini štetja pokritosti, vsak pod svojim
  imenom, številka manjkajočih artiklov ter kako so bili dokumenti povezani z
  vašimi izdelki.
- **Queue** — koliko dela čaka, razčlenjeno po vrsti. Predvsem za razvijalca.
- **Spend** — denar. Glejte spodaj.
- **Recent jobs** — surov seznam vsega, kar je sistem nedavno naredil.
  Namenjen razvijalcu za iskanje, ne za dnevno branje.

### Coverage, podrobneje

Zavihek **Coverage** hrani štiri druge načine štetja pokritosti. Noben od njih
ni stavek o izjavah na vrhu; vsak odgovarja na drugo vprašanje:

| Stolpec | Kar šteje |
|---|---|
| **Strict** | Samo izdelki, ki jih je Business Central jasno označil kot medicinske pripomočke |
| **Processed scope** | Enako, a šteje tudi izdelke, ki jih Business Central še ni razvrstil. Širše, previdnejše štetje |
| **Device (MDR/MDD)** | Enaka ozka skupina kot pri Strict, a šteje samo dokument, napisan za medicinske pripomočke. Certifikat sistema kakovosti sam zase tu ne šteje — pove, da dobavitelj vodi ustrezen sistem kakovosti, ne da ta izdelek izpolnjuje predpise |
| **Declared (DoC)** | Enaka ozka skupina, a šteje samo izjavo o skladnosti, ki ni potekla |

Prvi trije štejejo katerikoli veljaven objavljen dokument; o izjavah sprašujeta
samo **Declared (DoC)** in stavek na vrhu.

Pod tem **Missing mfr_ref** ponovi ploščico kot manjka / skupaj / delež,
spodnja razpredelnica pa prikazuje, kako so bili vaši objavljeni dokumenti
povezani z vašimi izdelki — za ime tega razdelka na strani glejte prevajalno
razpredelnico spodaj, za pomen posamezne vrstice pa
[How it was matched](../glossary.sl.md#besede-o-tem-kako-je-dokument-povezan-z-artiklom)
v slovarju. Oboje je informativno.

### Spend, podrobneje

Ploščica **30d of €…** prikazuje znesek, porabljen v zadnjih 30 dneh, glede na
mesečno omejitev. Zavihek **Spend** doda še drugo številko: skupno porabo
doslej, glede na ločeno, celokupno omejitev.

**Nobena od omejitev ničesar samodejno ne ustavi.** Prekoračitev katerekoli od
njiju sistema ne ustavi pri delu. To je številka, ki jo velja opaziti in
omeniti razvijalcu, ne alarm.

Če zavihek prikaže opombo, da nekaterim klicem ni zabeležena cena, gre za
vrzel v lastnem knjigovodstvu sistema — razvijalčev problem, ne vaš.

---

## Kaj lahko gre narobe

| Kar vidite | Kar to pomeni | Kaj storiti | Dobro ali slabo |
|---|---|---|---|
| Številka izjav pada | Manj vaših medicinskih pripomočkov ima izjavo | Vredno vprašati. Običajno so iz Business Centrala prispeli novi artikli, za katere izjave še ni | Slabo, a ni nujno |
| **to review** hitro narašča | Sistem najde več, kot lahko obdelate | Preživite čas v Review. Če vas prehiteva naprej, povejte razvijalcu — verjetno je dobavitelj spremenil način objavljanja | Vaše, da obdelate |
| **manual queue** narašča | Več stvari potrebuje človeka | Odprite [Manual](manual.sl.md) in preverite, katere vrstice so res vaše | Vaše, da obdelate |
| **dead jobs** nad nič | Neko opravilo je večkrat spodletelo in obupalo | Odprite [Failed tasks](failed.sl.md). Prva dva razdelka imata vsak svoj gumb in sta vaša; tretji je razvijalčev, njegov **Re-run all** pa je operaterski gumb | Razvijalčevo, če ponovni zagon tega ne odpravi |
| **sweep due** nad nič | Dobavitelj je na vrsti za preverjanje EUDAMED | Odprite ploščico in pritisnite **Start the check**, ko imate čas | Vaše, ni nujno |
| **Documents being read** visoko in se ne premika | Zdi se, da je stroj zaposlen, a nič se ne dokonča | Tega ne diagnosticirajte sami. Povejte razvijalcu | Razvijalčevo |
| 30-dnevna poraba blizu ali nad omejitvijo | Sistem ta mesec stane več kot običajno | Omenite. Ne poskušajte ničesar ustaviti sami | Razvijalčevo, da presodi, vaše, da opazite |

---

## Besede, ki jih uporablja zaslon
| Kar piše na strani | Kar to pomeni |
|---|---|
| **System status** | Naslov na vrhu te strani in njeno ime v bloku Operator v meniju. Do 14. septembra 2026 se je glasil "Queue status" |
| **mfr_ref** | Dobaviteljeva lastna številka artikla za izdelek |
| **match_basis**, and rows like `ref-list`, `name-family` | Kako je bil dokument povezan z izdelkom. Vsi drugi zasloni ta stolpec naslovijo **How it was matched**. Glejte [slovar](../glossary.sl.md#besede-o-tem-kako-je-dokument-povezan-z-artiklom) |
| **staged links on production (C5)** | Povezave med dokumentom in izdelkom, ki še čakajo na človeka, čeprav dokument sam že šteje. Informativno — tu ni ničesar za klikniti. **C5** je interna številka pravila; ne upoštevajte je |
| **pending / running / done / failed / dead** | Faze enega dela sistemovega lastnega dela. Glejte [slovar](../glossary.sl.md) |
| Raw task names like `ingest.run`, `fetch.url`, `extract.doc` | Katere vrste dela gre pri enem opravilu. Namenjeno razvijalcu; berljivo ime faze (INGEST, FETCH, EXTRACT …) zraven je različica za branje |
| **dedupe key** | Interna koda, ki prepreči, da bi se isto opravilo zagnalo dvakrat. Ni nekaj, kar bi morali brati |
| **priority** — `interactive` / `delta` / `sweep` | Zakaj opravilo obstaja: `interactive` pomeni, da ga je pravkar sprožil klik osebe; `delta` in `sweep` pomenita, da ga je sprožil lastni urnik sistema |
| **unpriced calls (pricing gap)** | Poraba, ki ji sistem ni znal določiti cene. Vrzel v knjigovodstvu, ne strošek, ki bi ga dolgovali |

---

## Sorodno
- [Today](today.sl.md): prvi zaslon pisarne, ki nosi isti stavek o izjavah
- [Processing](processing.sl.md): število **Documents being read**, po fazah
- [Coverage gaps](coverage.sl.md): artikli za stavkom o izjavah
- [Review](review.sl.md) — kamor vas odpelje **to review**
- [Manual](manual.sl.md) — kamor vas odpelje **manual queue**
- [Failed](failed.sl.md) — kamor vas odpelje **dead jobs**
- [Data quality](data-quality.sl.md) — preverjanja kataloga in razreda
  pripomočka, ki jih ta stran ne pokriva
- [Glossary](../glossary.sl.md) — vse besede na enem mestu
