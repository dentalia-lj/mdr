# Upload

**V enem stavku:** tu sistemu ročno predate dokumente, enega ali več hkrati.

## Stanje

Deluje. Preverjeno s kodo 9. 11. 2026.

## Ali lahko tu kaj pokvarim?

Ne.

- Nič tukaj ne izbriše datoteke. Ko naložite dokument, ostane shranjen —
  ne glede na to, kaj sistem kasneje sklene o njem.
- Če isto datoteko naložite dvakrat, se ne ustvari druga kopija.
- Vaš dokument sam po sebi ne šteje k ničemur. Sistem je bodisi dovolj
  prepričan in dokument šteje takoj, ali pa počaka na osebo na strani
  **Review** — enako pravilo kot za vsak dokument, ki ga sistem najde sam.
- Edina stvar, pri kateri je potrebna previdnost: če pridete s kartice na
  strani **Missing documents**, je stran nastavljena na artikel s te
  kartice. Dokument za drug artikel, naložen od tam, bo predlagan za napačen
  artikel, in vsaka datoteka, ki jo dodate na tej strani, gre k temu artiklu.
  A tudi tedaj se nič ne izbriše: napaka se popravi z zavrnitvijo na strani
  **Review**.
- Če je v vrstici datoteke napaka, ta datoteka ni bila shranjena. Na druge
  datoteke na seznamu to ne vpliva.

## Kaj je to

Tu sistemu predate dokumente — izjave, certifikate ali navodila za
uporabo — ki ste jih našli sami, namesto da bi jih sistem našel sam. Dodate
lahko več PDF-jev hkrati; vsak se prebere in obravnava posebej.

## Kdaj to uporabite

Imate dokument — od dobavitelja, iz e-pošte ali od koderkoli — in želite,
da ga sistem prebere in poveže z vašimi izdelki. Ali pa ste kliknili
**Upload what I found** na kartici na strani **Missing documents** (ali
**Upload document** na zaslonu **Manual**), ker je sistem sam iskal
dokument in ga ni našel.

## Preden začnete

- Datoteke morajo biti shranjene na vašem računalniku ali napravi.
- Vsaka mora biti PDF. Sistem sprejme datoteko le, če se njeno ime konča na
  `.pdf`.
- Vsaka mora biti manjša od 25 MB. To je današnja privzeta omejitev; razvijalec
  jo lahko dvigne. Če je datoteka zavrnjena, sporočilo na zaslonu navaja
  dejansko veljavno omejitev.
- Naenkrat lahko dodate največ 50 datotek.
- Ni vam treba vedeti, kateri artikel dokument pokriva. Sistem to ugotovi
  iz številk artiklov, natisnjenih v PDF-ju.
- Če pridete s kartice na strani **Missing documents**, stran artikel že
  pozna: dve skriti polji nosita artikel in kartico. Vanju nikoli ne
  vpisujete sami.

## Kaj storite

1. Kliknite **Upload a document** v meniju na levi ali **Upload what I found** na
   kartici na strani **Missing documents**.
2. Če ste prišli s kartice, vrstica pod naslovom navede artikel, na primer
   "Uploading a document for FUJI PLUS CAPSULES (GC, item 003234)".
   Preverite, ali je to artikel, za katerega imate dokument.
3. Povlecite datoteke PDF na stran ali kliknite **Choose files** in izberite
   eno ali več. Nič se še ne pošlje: datoteke se pojavijo na seznamu.
4. Če datoteka ne sodi zraven, v njeni vrstici kliknite **Remove**.
5. Kliknite **Upload N files**. Datoteke gredo druga za drugo; ostanite na
   strani, dokler seznam nad gumbom ni prazen ali kaže le še težave.

## Kaj se zgodi nato

Stran se ne osveži znova. Vsaka prispela datoteka se premakne navzdol v
**Recent uploads**, kjer piše, kaj se je z njo zgodilo. Seznam se sam osvežuje,
dokler se kakšna datoteka še bere, in ostane tam tudi, če stran zapustite in se
vrnete. Prikazuje zadnjih 100 nalaganj vseh uporabnikov.

| Recent uploads pravi | Pomeni | Kaj lahko storite |
|---|---|---|
| Being read | Sistem dela na tem. Navadno kakšno minuto, dlje, če je zaseden | Počakajte ali se vrnite pozneje |
| Waiting for review | Pogledati mora oseba | **Open in Review** |
| Published | Dokument šteje | **Open document** |
| On file | Pravilno prebran, a ne pokriva ničesar, kar prodajate | **Open document** |
| Replaced | Starejša različica dokumenta, ki ga že imate | **Open document** |
| Rejected | To datoteko je nekdo že prej zavrnil | **Open document** |
| Nothing filed: Not a compliance document | Ni izjava, certifikat ali navodilo za uporabo | Preverite, ali ste izbrali pravo datoteko |
| Nothing filed: Could not read enough | Sistem iz nje ni mogel dobiti potrebnih podatkov | Poskusite z bolj čitljivo kopijo ali se obrnite na razvijalca |
| Nothing filed: Read before, no document came of it | Ista datoteka je že prišla in iz nje ni nastal dokument | Preverite, ali ste izbrali pravo datoteko |
| Failed | Nekaj se je pokvarilo na strani sistema | Obrnite se na razvijalca |
| No result | Minil je dan brez odgovora | Obrnite se na razvijalca |

- Vaša datoteka je trajno shranjena, ne glede na to, kaj se zgodi potem.
- Če je bila ista datoteka že prej naložena in stran ni navedla artikla,
  se ne zgodi nič več: sistem jo že ima.
- Sicer jo sistem prebere in poskusi povezati z vašimi izdelki, enako kot
  vse, kar najde sam. Če je prepričan, dokument šteje takoj. Če ni,
  počaka na vas na strani **Review**.
- Če ste prišli s kartice na strani **Missing documents** ali iz zahteve
  **Manual**, ta izgine s svojega seznama, ko iz ene od vaših datotek nastane
  dokument. Napačna datoteka kartico pusti na mestu. Če je dokument na strani
  **Review** zavrnjen, se kartica vrne.

## Kaj lahko gre narobe

| Vidite | Pomeni | Kaj storiti | Dobro ali slabo |
|---|---|---|---|
| *only PDF uploads are accepted* | Izbrana datoteka ni PDF ali se njeno ime ne konča na `.pdf` | Shranite ali izvozite dokument kot PDF in poskusite znova | Slabo — nič ni bilo shranjeno |
| *that file is named .pdf but its contents are not a PDF* | Datoteka se konča na `.pdf`, a znotraj ni PDF. Skoraj vedno gre za spletno stran, shranjeno iz brskalnika: shranila se je stran, ne dokument, na katerega stran kaže | Znova odprite stran, z desnim klikom na povezavo do dokumenta izberite **Shrani povezavo kot** in naložite tisto datoteko | Slabo, a dobro ujeto: pred septembrom 2026 se je taka datoteka vložila kot prava izjava |
| *file exceeds the 25 MB limit* | Vaša datoteka je večja, kot jo sistem trenutno dovoljuje | Prosite razvijalca za dvig omejitve ali pošljite manjšo datoteko | Slabo — nič ni bilo shranjeno |
| *Failed to send* z gumbom **Try again** | Datoteka ni prišla do sistema, navadno zaradi prekinjene povezave | V tej vrstici kliknite **Try again** | Slabo — za to datoteko ni bilo shranjeno nič, druge so v redu |
| *This is a folder* | Spustili ste mapo namesto datotek | Odprite mapo in spustite datoteke PDF iz nje | Slabo — nič ni bilo poslano |
| *already added, not added again* | Isto datoteko ste dodali dvakrat | Ni treba storiti ničesar | V redu |
| Sporočilo o napaki, ki ga ne prepoznate | Nekaj je spodletelo na strani sistema | Nič ni bilo shranjeno. Poskusite znova ali se obrnite na razvijalca | Slabo, a varno |

## Besede, ki jih uporablja zaslon

| Zaslon pravi | Pomeni |
|---|---|
| "A PDF you already have: the system reads it, and Recent uploads below says what came of it" | Vrstica pod naslovom, ki pove, kaj ta zaslon počne |
| "Uploading a document for …" | Artikel, za katerega je to nalaganje, poimenovan s kartice na strani **Missing documents**, s katere ste prišli |
| "Every file below goes to this item" | Prišli ste s kartice na strani **Missing documents**: vse datoteke, ki jih dodate tukaj, so predlagane za ta artikel |
| "Waiting", "Uploading..." | Datoteka je na seznamu in še ni poslana ali pa se pravkar pošilja |
| "Technical details" | Odpre številko opravila za vašim potrdilom. Uporabna le, če morate o njej vprašati razvijalca |
| "Already in progress." | To nalaganje se že bere. Vaš pritisk ni ničesar spremenil |

## Sorodno

- [Review](review.sl.md) — kje mora naloženi dokument navadno počakati na vašo odločitev
- [Missing documents](missing.sl.md): kjer je gumb **Upload what I found**
- [Manual](manual.sl.md) — od kod prihaja povezava **Upload document**
- [Documents](documents.sl.md) — kje se pojavi odobrena naložena datoteka
- [Glossary](../glossary.sl.md) — vse besede na enem mestu
