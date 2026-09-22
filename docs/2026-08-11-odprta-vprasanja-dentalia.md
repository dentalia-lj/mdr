# Odprta vprašanja za Dentalio — 11. avgust 2026

Vse spodaj navedeno zavira ali slabša samodejno obdelavo. Številke so izmerjene na izvozu
ljubljanskega kataloga (`Artikli 3.7.2026.xlsx`, 19.091 vrstic, od tega 15.958 v obdelavi) in
na 1.307 datotekah PDF v mapah na SFTP. Preverjeno 11. 8. 2026, ne ocenjeno.

Pri vsakem vprašanju je navedeno, na katero polje v Business Centralu se nanaša.

Sklopa A in B sta tista, ki dejansko preprečujeta, da bi se dokumenti sploh pripeli na artikle.

---

## A. Kdo je proizvajalec

**Polji:** `Šifra proizvajalca` (koda proizvajalca na artiklu) in `Ime` v šifrantu
`Proizvajalci.xlsx` (390 vrstic).

Dokument povežemo z artiklom prek imena proizvajalca in številke artikla. Vaše ime, naše ime in
pravno ime, natisnjeno na izjavi, so trije različni nizi. V enem primeru nimajo niti ene skupne
besede.

1. **Ali je Komet isto podjetje kot Gebr. Brasseler GmbH & Co. KG?**
   Vse Kometove izjave, ki smo jih prebrali, so podpisane *Gebr. Brasseler GmbH & Co. KG*, in
   sicer 110 od 186 datotek. Beseda "Komet" se kot proizvajalec ne pojavi nikoli. Dveh imen brez
   skupne besede ne more povezati noben samodejni postopek. Dokler tega ne potrdite, ostane
   186 dokumentov nepripetih na 319 artiklov (šifra `077`).

2. **Dentaurum: en proizvajalec ali trije?**
   V vašem šifrantu so to tri ločena imena: DENTAURUM ORD (`396`, 77 artiklov),
   DENTAURUM TEH (`397`, 64) in DENTAURUM IMPLANTS (`10026`, 29). Zato jih obravnavamo kot tri
   proizvajalce. Pri Ivoclarju je ravno obratno: šifre `001`, `005` in `275` imajo vse ime
   IVOCLAR VIVADENT in smo jih združili v enega. Radi bi uporabili eno pravilo, ne dveh.

3. **Ali naj uporabimo vaše ime "IVOCLAR VIVADENT" namesto našega krajšega "IVOCLAR"?**
   Vaše ime iz šifranta je bližje temu, kar dejansko piše na dokumentih (*Ivoclar Vivadent AG*,
   124 od 174 datotek), kot ime, ki smo ga izbrali sami. Raje se držimo vašega poimenovanja.

4. **Dentsply je sedem vaših šifer pod enim koncernom. Kako naj to zabeležimo?**
   `010` VDW (164 artiklov), `012` SIRONA (29), `022` MAILLEFER (74), `035` DENTSPLY (52),
   `115` RINN (1), `313` SCHICK (3), `10153` ANKYLOS (2). Skupaj 325 artiklov.
   Za nabavo je vsaka svoj proizvajalec in to je pravilno. Lahko vsaki blagovni znamki damo
   svojo identiteto ali pa izrecno zabeležimo razmerje do matičnega podjetja. Česar ne smemo
   narediti, je tiho združevanje: certifikat Maillefer ne sme nikoli pristati na artiklu
   Dentsply. Dokler to ni odločeno, gre 86 dokumentov Maillefer in Sirona v ročno vrsto,
   namesto da bi jih napačno pripeli.

5. **Potrdite, prosim, da je Dentsply IH Limited pooblaščeni zastopnik za VB in ne proizvajalec.**
   Pojavi se v 66 datotekah, vedno v polju Authorised Representative. Namenoma smo ga izločili.
   Če se motimo, je teh 66 dokumentov trenutno brez pripisanega proizvajalca.

6. **Ali je GC Corporation (Japonska) drug proizvajalec kot GC Europe N.V.?**
   GC Europe N.V. podpisuje 134 od 149 datotek, GC Corporation se pojavi v 6. Če tudi japonska
   matična družba izdeluje artikle, ki jih imate na zalogi, potrebuje svojo identiteto.

7. **CEFLA se v katalogu pojavi dvakrat.**
   Enkrat kot šifra `10015` (232 artiklov) in enkrat tako, da je v polju `Šifra proizvajalca`
   namesto šifre zapisano besedilo "CEFLA" (363 artiklov). Je to en proizvajalec pod dvema
   identitetama ali res dva?

8. **HENRY SCHEIN (šifra `004`) ima 1.114 artiklov, od tega so samo 3 razvrščeni kot
   medicinski pripomoček.** Henry Schein je distributer. Čigave izjave naj iščemo za te
   artikle: dejanskega proizvajalca ali dokumente lastne blagovne znamke Henry Schein?

9. **Je šifra `081` CARL MARTIN proizvajalec ali zbirna šifra za instrumente razreda I?**
   2.567 artiklov, kar je 60 odstotkov vseh potrjenih pripomočkov v katalogu. Vsi imajo razred
   `RAZRED IR`, imena so splošna (klešče za ekstrakcijo, škarje za prevleke, strgalo), 2.276 od
   njih (89 odstotkov) pa nima dobaviteljeve številke artikla. Če gre za zbirno šifro, potem
   večina vaših potrjenih pripomočkov nima niti proizvajalca, pri katerem bi dokumente iskali,
   niti številke, po kateri bi jih povezali. To bistveno spremeni pričakovano pokritost.

10. **Polje `Šifra proizvajalca (primarni)` je izpolnjeno v 16 vrsticah od 19.091**, in sicer z
    vrednostma `077-A` (14) in `077-B` (2). Nikoli se ne ujema z glavnim poljem. Je to opuščeno
    polje ali nekaj, kar bi morali upoštevati?

---

## B. Razvrstitev v razred medicinskega pripomočka

**Polje:** `Razred medicinskega pripomočka`

Dejanske vrednosti v izvozu, vse vrstice:

| vrednost | vrstic |
|---|---|
| prazno | 11.693 |
| `NI MP` | 3.133 |
| `RAZRED IR` | 2.569 |
| `RAZRED IIA` | 1.594 |
| `RAZRED I` | 69 |
| `RAZRED IIB` | 33 |

11. **11.693 vrstic ima to polje prazno, kar je 61 odstotkov kataloga oziroma 73 odstotkov
    tistega, kar obdelujemo.** Vrstice z `NI MP` izločimo takoj, ker so izrecno označene kot
    nepripomoček. Praznih ne moremo izločiti, ker prazno ni isto kot "ni pripomoček". Trenutno
    jih obdelujemo vse, ker je izpustiti pravi pripomoček dražja napaka. Vprašanje: ali prazno
    polje pomeni "še ni razvrščeno" ali "ni pripomoček"? Kdo ga lahko izpolni in v kakšnem roku?

12. **Najostrejši primer: celotna družina Dentsply nima nobenega potrjenega pripomočka.**
    Vseh 325 artiklov ima to polje prazno ali `NI MP`. Hkrati pa je v njihovi mapi 405 datotek
    PDF, kar je 31 odstotkov celotnega korpusa dokumentov, in 147 med njimi se izrecno sklicuje
    na MDR ali MDD. Eden od obeh zapisov ni pravilen. Enako velja za VOCO (0 od 31 artiklov
    razvrščenih) in za DENTSPLY `035` (1 od 53).

---

## C. Številke artiklov, ključ za povezovanje

**Polji:** `Dobaviteljeva št. artikla` (primarno) in `Št.` (interna številka artikla)

13. **7.082 od 15.958 artiklov (44 odstotkov) nima dobaviteljeve številke artikla.**
    Izjava navaja artikle, ki jih pokriva, prav po tej številki. Brez nje ni ničesar, na kar bi
    se lahko povezali. Take artikle je mogoče pokriti samo ročno ali pa številke dodati v BC.

14. **364 zapisov v polju `Dobaviteljeva št. artikla` vsebuje opombo namesto številke.**
    `NE BO VEČ NA ZALOGI!` (122), `OPERA!` (48), `NI VEČ DOBAVLJIVO!` (27), `NI DOBAVLJIVO!!!`
    (26), `NE NAROČAJ!` (15), `samo po naročilu!` (11) in podobno. Sistem te vrednosti primerja
    s številkami artiklov na dokumentih, kot da bi bile šifre. Je polje mogoče očistiti v BC ali
    naj jih filtriramo na naši strani?

15. **Potrdite, prosim, da povezujemo po osnovni številki artikla in da pripone za trg
    ignoriramo.** Dokumenti naštejejo vse tržne različice istega artikla, na primer `645986` se
    pojavi kot DC, DS, EA, EG, ES, EU, FC, FS, IS, JJ, KS, PB, PP, PS, RS, SS. V BC je zapisana
    osnovna številka. Prav zato se trenutno ujema le 19 od 174 Ivoclarjevih dokumentov, ob
    ignoriranju pripone pa 54. Eno opozorilo: Kometove šifre svedrov po ISO (`104 H251EF 060`)
    vsebujejo črke, ki so del številke, zato je treba to nastaviti za vsakega proizvajalca
    posebej.

16. **Straumannove različice pakiranj.** Artikla `061.7312` in `061.7314` oba ustrezata osnovni
    številki `061.7310`, ki je tista, ki jo navaja izjava. Je ta vzorec splošen pri Straumannu
    ali velja le za posamezne artikle?

---

## D. Korpus dokumentov

**Vir:** mape na SFTP (`mail.dentalia.si`)

17. **Na SFTP je 1.307 datotek PDF, razporejenih le v 12 map.**
    DENSTPLY 405, VOCO 292, KOMET 186, IVOCLAR 174, GC 149, DENTAURUM 37, NEODENT 34,
    STRAUMANN 13, PLANMECA 8, 3SHAPE 7, BREDENT 1, LUMIWHITE 1.
    Teh 12 znamk pokriva 4.630 od 15.958 artiklov v katalogu. Je to vse, kar imate, ali so
    dokumenti še kje drugje (e-pošta, skupni diski, portali dobaviteljev) za preostalih približno
    370 šifer proizvajalcev?

18. **Dve razmerji med številom dokumentov in artiklov izstopata, vsako v svojo smer.**
    VOCO: 292 dokumentov za 31 artiklov v katalogu. Ivoclar: 174 dokumentov za 1.294 artiklov.
    Nobeno samo po sebi ni nemogoče, a eno od njiju najbrž pomeni bodisi manjkajoče artikle v
    katalogu bodisi dokumente za izdelke, ki jih ne prodajate več. Katero od obojega?

19. **Dentaurumovih 37 dokumentov še ni prebranih** in Dentaurum še nima preslikave imena, zato
    bodo ob prvem zagonu šli v ročno vrsto. Prebrali jih bomo pred naslednjim pregledom. Navajamo
    zato, da tega ne razumete kot napako.

20. **Kaj naj naredimo z dokumenti v mapah, ki niso medicinski pripomočki?**
    Kozmetika (LUMIWHITE, Uredba 1223/2009), izjave za baterije in REACH (VOCO) ter izjave po
    direktivi o nizki napetosti (Dentsply). Jih shranimo zaradi popolnosti ali zavržemo?

21. **Ali je "Declaration of Compatibility for Systems/Procedure Packs" (MDR, člen 22) tip
    dokumenta, ki ga morate spremljati?** Najmanj 17 Dentsplyjevih dokumentov za endodontske
    komplete je te vrste. Ni izjava o skladnosti in ni certifikat.

22. **Ali so preglednice na SFTP še vzdrževane in verodostojne?**
    IVOCLAR "MD or NOT.xlsx" in "Copy of Dentalia_UDI.xlsx", KOMET "Where to find DoC.xlsx"
    (3.801 vrstic, številka artikla proti dokumentu), GC "Devices by Class.xlsx". Če so ažurne,
    neposredno odgovorijo na več zgornjih vprašanj in prihranijo veliko ročnega dela. Če so
    zastarele, jih raje ne uporabimo, kot da bi jim zaupali.

---

## E. Veljavnost in obnavljanje

**Polji v našem registru:** `validity_from`, `validity_to` (datum poteka na dokumentu)

23. **Izjava o skladnosti po predpisih nima datuma poteka.**
    MDR, Priloga IV, zahteva samo kraj in datum izdaje. Datum poteka nosijo le certifikati
    priglašenih organov (člen 56 jih omejuje na največ pet let). Pravila "vsak certifikat ima
    datum veljavnosti, ob približevanju ga zamenjamo" torej ni mogoče uporabiti za izjave.
    Potrebujemo vašo odločitev med dvema možnostma: da spremljamo *certifikat*, na katerega se
    izjava sklicuje, in izjavo štejemo za veljavno, dokler velja ta certifikat, ali da določimo
    rok zastaranja ("vsako izjavo, starejšo od N let, zahtevamo znova"). Katero želite?

24. **Pripomočki razreda I s samocertificiranjem sploh nimajo certifikata.** Ni priglašenega
    organa, ni datuma poteka, ni česa terjati. Potrdite, prosim, da so izvzeti iz zanke
    obnavljanja in ne ostanejo v njej trajno nerazrešeni.

25. **Koliko časa vnaprej naj gre zahtevek za obnovo, po posameznem tipu dokumenta?**
    Na primer 90 dni pred potekom certifikata. Za vklop opomnikov potrebujemo eno število na tip.

---

## F. Dostopi in obseg (danes ne zavira, potrebno pred naslednjo fazo)

**Polje:** `Blokirano` (točka 26)

26. **Blokirani artikli (`Blokirano = 1`):** 678 vrstic, od tega jih 43 BC izrecno razvršča kot
    pripomočke. Trenutno so zunaj obsega. Potrdite, prosim. Če je bil kateri od njih v preteklosti
    prodan, za njegove dokumente še vedno velja hramba po MDR, tega pa iz izvoza ni mogoče
    ugotoviti.

27. **Zagreb:** je to isti Business Central in isto oštevilčenje artiklov ali ločen sistem?
    Odgovor določa velikost druge faze.

28. **Kje naj bo arhiv dokumentov?** Potrebujemo mapo na Google Drive z dostopom ali potrditev,
    da zadošča naša lastna hramba.

29. **Dostop do poštnega predala** za samodejna sporočila o obnovi: kateri naslov pošilja in kako
    se nanj povežemo. Odloženo, a je zadnji del zanke obnavljanja.
