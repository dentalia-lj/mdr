# Vprašanja za Dentalio — 3. krog (stanje 18. 8. 2026)

Hvala za odgovore. Ta različica dokumenta ohranja **iste številke vprašanj** kot
prejšnja, da se sklici ne podrejo. Vsako vprašanje ima zdaj zapisan vaš odgovor;
kjer je odgovor popoln, je označeno **ZAPRTO** in od vas ne potrebujem ničesar več.
Kjer je označeno **ODPRTO**, spodaj piše natanko, kaj še manjka.

Številke so izmerjene 17. in 18. 8. 2026: iz izvoza kataloga (15.958 obdelanih
artiklov od 19.091 vrstic), iz 1.307 dokumentov PDF na SFTP in iz mojega registra.

**Na kratko:** zaprtih je 13 vprašanj, odprtih ostaja 6. Najbolj me ustavljata dve
stvari — cenik KOMET, ki ni prispel (vprašanje 8), in nasprotje med odgovoroma o
petletni veljavnosti in razredu I (vprašanji 14 in 16).

---

## Zaprto — hvala, tu je vse jasno

| # | Vprašanje | Vaš odgovor | Kaj naredim |
|---|---|---|---|
| 2 | 3SHAPE — en proizvajalec ali trije | "Vsi trije so isti proizvajalec" | Vse tri šifre (10003/10004/10005) združim v eno identiteto; dokument ene velja za vse. 197 artiklov, 7 dokumentov, ki danes niso pripeti nikamor. |
| 3 | GC Corporation in GC Europe N.V. | "GC je enako oboje" | Obe imeni vodita na istega proizvajalca; 6 dokumentov, ki so podpisani kot GC Corporation, se odslej pripne. |
| 4 | CARL MARTIN (081) | "Absolutno proizvajalec – tudi direktno od njih iz Nemčije dobimo robo" | Največja posamezna postavka projekta: 2.567 artiklov (60 % vseh potrjenih pripomočkov), za katere danes **nimam nobenega dokumenta**. Začnem z iskanjem dokumentov pri Carl Martinu. Opomba: v šifrantu obstaja tudi šifra 283 MARTIN GEBRUEDER — če gre za isto skupino (KLS Martin), naj bosta ena identiteta, sicer se dokumenti razdelijo na dvoje. |
| 6 | Dentsply — nič pripomočkov, 405 dokumentov | "Dokumenti so pravilni – Dentsply Sirona ima več brandov" | Blagovne znamke ostanejo ločene, krovno podjetje zabeleženo kot razmerje. Manjkajoča razvrstitev je del vprašanja 5. |
| 7 | Straumann — različice pakiranja | "Gre za različne artikle – različne dimenzije" | **Nič ne spreminjam.** Sistem že zdaj zahteva točno ujemanje številke, kar je natanko to. Posledica: izjava, ki navaja `061.7310`, sama po sebi ne pokrije `061.7312` in `061.7314`. |
| 9 | Razmerje med dokumenti in artikli | "Bolje več kot premalo. Če artikel ni MP, tega dokumenta ne rabimo." | Odvečne dokumente hranim ločeno (stanje `filed`), v pokritost ne štejejo. |
| 11 | Izjave po členu 22 (kompleti) | "Lahko se doda tudi to – posebna mapa" | Dodam kot svojo vrsto dokumenta in svoj filter v vmesniku. Popravek: ločene *mape* v arhivu ne morem narediti po vrsti dokumenta — arhiv se zloži ob prenosu, vrsta pa je znana šele po branju. Ločen seznam v vmesniku da isti učinek. |
| 10 | Dokumenti, ki niso o medicinskih pripomočkih | "Želimo jih" (18. 8. 2026) | Hranim jih, ločeno označene: v arhivu in vezani na proizvajalca, izven pokritosti pripomočkov in izven zanke obnavljanja. Izjeme, ki ostanejo dokazilo: certifikati ISO 13485, ki nosijo `n.a.` samo zato, ker ISO ni MDR. |
| 13 | Dokumenti brez povezanega artikla (178 od 325) | "Vezani na proizvajalca — obdržimo" (18. 8. 2026) | Ostanejo v stanju `filed`, pripeti proizvajalcu, izven pokritosti artiklov. Ko pozneje uvrstite artikel, ki ga tak dokument pokriva, naj se pripne samodejno — to gradim posebej, danes se to ne zgodi. |
| 1 | Kdo je proizvajalec artiklov pod šifro 004 | "Henry Schein ima svoj brand … za ostale je dobavitelj" | Za pripis proizvajalca velja **vaš zapis v BC**: kar je v katalogu pod 004, vodim kot Henry Schein. Popravek se naredi v BC, ne pri meni. Spletno stran henryschein.de uporabljam le kot vir za iskanje dokumentov. |
| 12 | So preglednice na SFTP zanesljive | delovne datoteke, ne uradni vir | Nanje ne vežem nobenega postopka. Preslikavo šifer KOMET sem iz njih izpeljal enkrat kot pravilo; same datoteke sistem ne bere. |
| 14 | Izjava brez datuma poteka | "Kjer je datum napisan upoštevaj datum – kjer ga ni upoštevaj + 5 let za potek" | Vgradim: uporabim zapisani datum, sicer datum izdaje + 5 let. Datuma ne zapišem v dokument (dokument tega ne trdi), ampak ga izračunam ob preverjanju. **Glejte vprašanje 16 — ta odgovor in odgovor o razredu I si nasprotujeta.** |
| 17 | Koliko dni pred potekom | "Mesec dni prej" | Nastavim 30 dni za vse tri vrste dokumentov. Če se izkaže, da je za pridobitev dokumenta premalo, je to ena nastavitev in jo kadarkoli spremenimo. |

---

## Odprto — tu še potrebujem odgovor

### 5. Razred pripomočka — kdo ga bo izpolnil

**Vaš odgovor:** *"Nimamo podatka o razredu – ta podatek je na teh dokumentih, ki
jih iščemo."*

**Kaj sem preveril (18. 8. 2026).** Preveril sem vseh 1.303 berljivih dokumentov v
mapah, ali je razred sploh razviden iz njih. Odgovor je: **je, in sicer v treh
oblikah** — kot označeno polje (IVOCLAR "EU Risk Class … Class IIa" v 122
datotekah; VOCO "Risk class" v 110), kot **stolpec v tabeli po posameznem
artiklu** (KOMET, stolpec "MD Class" v 70 datotekah seznamov) in v nemški obliki
("Klasse IIa, Regel 6"). Skupno **623 od 1.027 datotek z berljivim besedilom
navaja razred**; 276 datotek je skeniranih slik brez besedila (od tega 201
Dentsply). Poleg tega imata GC in IVOCLAR na SFTP preglednici, ki nosita razred po
artiklu neposredno (GC `Devices by Class`, 3.098 vrstic; IVOCLAR `MD or NOT`,
8.353 vrstic).

**Kje pa to ne pomaga.** 11.693 artiklov nima razreda. **9.763 od njih (83 %) je
pri proizvajalcih, za katere nimam nobenega dokumenta** — največ pri 004 HENRY
SCHEIN (1.111), 002 STRAUMANN (918), 160 NEODENT (796), CEFLA (595 v dveh šifrah),
053 IMES-ICORE (331), 043 INTERDENT (232). Razred torej pride skupaj z dokumenti,
proizvajalec za proizvajalcem — sam po sebi ne more zapolniti stolpca.

**Kaj predlagam:** razred beležim kot podatek, izluščen iz dokumenta, in vam
pripravim **predlog razredov po artiklih** (seznam za uvoz v BC). Vpisujem ga ne
jaz, ampak vi v BC — BC ostane vir resnice za katalog.

- [ ] Da, pripravite predlog razredov za uvoz v BC (kdo ga pri vas prevzame:
      ______________________)
- [ ] Ne, razred vodite samo pri sebi, v BC ga ne uvažamo
- [ ] Drugače: ______________________

**Odgovor:**

**Drugo podvprašanje:** dokler je stolpec prazen, merim pokritost samo na 4.265
potrjenih pripomočkih, ne na celotnem katalogu. Se strinjate, da je to za zdaj
prava številka?

**Odgovor:**

---

### 8. KOMET — cenik

**Vaš odgovor:** *"Prilagam cenik Komet, ki je lahko osnova za iskanje dokumentov.
Šifro smo prilagodili glede na ostale že prej določene."* In: *"Ne — komet je
specifičen"* (drugi proizvajalci nimajo take pretvorbe).

**Kje sem zdaj:** pretvorbo sem izpeljal in vgradil. Merjeno 18. 8. 2026 na vseh
186 dokumentih KOMET: **brez pravila se ne ujame noben artikel, s pravilom se jih
ujame 227 od 319.** Šifre torej berem neposredno iz vaših dokumentov in za
delovanje ne potrebujem nobene preglednice.

**Kaj še potrebujem:** cenik vseeno pošljite, če je pri roki — uporabim ga za
preverjanje (ali kje beremo napačno), ne kot vir podatkov. Prav tako me zanima,
ali je zamenjani vrstni red uradno pravilo (zapis po ISO 6360) ali dogovor med
vami in KOMET-om.

**Odgovor:** ______________________

---

### 15. Datum podpisa kot začetek veljavnosti — prosim za potrditev

**Vaš odgovor:** tega vprašanja ste izpustili.

**Zakaj je pomembno:** kadar dokument navaja samo kraj in datum podpisa
("Leuven, 12/02/2026"), ta datum od 13. 8. 2026 beležim kot **začetek
veljavnosti** dokumenta. Brez tega sistem ne more sam ugotoviti, kateri od dveh
dokumentov je novejši, in gre vsak tak primer človeku v pregled. Danes ima 178 od
306 izjav v registru vpisan datum začetka; prej ga ni imela nobena. Odločitev je
moja, zato prosim za potrditev ali ugovor.

- [ ] Potrjeno — datum izdaje/podpisa je začetek veljavnosti
- [ ] Ne — pustite prazno, dokler dokument izrecno ne navede obdobja veljavnosti

**Odgovor:**

---

### 16. Razred I in petletni rok — vaša odgovora si nasprotujeta

**Vaš odgovor:** *"Razred I mora imeti DOC – to moramo imeti. Ne rabi pa
Certifikata."* In pri vprašanju 14: *"Kjer ga ni upoštevaj + 5 let za potek."*

**V čem je nasprotje:** pripomoček razreda I s samopotrditvijo nima certifikata
priglašenega organa, njegova izjava o skladnosti pa praviloma nima datuma poteka.
Po petletnem pravilu bo torej vsaka taka izjava čez pet let od podpisa označena
kot potekla in bo sprožila zahtevo za obnovo. Prvotno vprašanje je predlagalo
ravno obratno — da so razred I izvzeti iz obnavljanja.

- [ ] **Da, tudi razred I obnavljamo** — izjavo osvežimo vsakih 5 let
- [ ] **Ne** — pri razredu I izjava velja, dokler se izdelek ne spremeni; iz zanke
      obnavljanja je izvzet
- [ ] Drugače: ______________________

**Odgovor:**

---

### 18. Poštni predal za zahteve po obnovi

**Vaš odgovor:** *"mdr@dentalia.si, outlookov predal – ne vem kako bi povezali z AI
ker nimamo Outlook 365."*

**Kaj to pomeni:** brez Microsoft 365 ne morem uporabiti njihovega API-ja. Ostaneta
dve poti in obe sta preprosti, potrebujem pa odgovor vašega IT:

- [ ] **IMAP z geslom za aplikacije** — če vaš strežnik to dopušča (to ve vaš IT
      oziroma ponudnik pošte)
- [ ] **Preusmeritev kopije** — vsa pošta na `mdr@dentalia.si` naj se v kopiji
      pošilja na naslov, ki ga pripravim jaz; pošiljanje ostane pri vas
- [ ] Drugače: ______________________

**Kdo pri vas to ve (ime in kontakt):** ______________________

---

### 19. Strežnik — podatki, ki jih potrebujem od Mitja

**Vaš odgovor:** *"Mitja ima že v akciji nek mikroserver za CRM za servis … na ta
server bi potem obesili tudi to MDR dokumentacijo – več info ima Mitja."*

**Kaj potrebujem od njega, da lahko ocenim postavitev:**

1. operacijski sistem in ali na njem teče Docker,
2. koliko prostora je na voljo za arhiv dokumentov (arhiv raste z vsakim
   prenesenim dokumentom in se hrani 10 let),
3. ali se strežnik varnostno kopira in kako,
4. kako je dosegljiv (samo v vašem omrežju, VPN, ali z interneta) — vmesnik danes
   teče za obratnim posredniškim strežnikom z geslom,
5. kdo ga upravlja in koga pokličem, ko kaj ne dela.

**Kontakt (e-pošta/telefon):** ______________________

---

## Kar potrebujem najprej

1. **Cenik KOMET** (vprašanje 8) — brez njega pretvorbe ne morem vgraditi, gre pa
   za 213 od 258 pripomočkov KOMET.
2. **Odločitev o razredu I** (vprašanje 16) — od nje je odvisno, ali petletno
   pravilo sploh lahko vklopim.
3. **Seznam artiklov lastne znamke Henry Schein** (vprašanje 1) — 1.114 artiklov
   je brez tega neobdelanih.
