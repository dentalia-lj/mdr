# Upload

**V enem stavku:** tu sistemu ročno predate en dokument.

## Stanje

Deluje. Preverjeno s kodo 15. 9. 2026.

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
  artikel. A tudi tedaj se nič ne izbriše: napaka se popravi z zavrnitvijo
  na strani **Review**.
- Če gumb vrne napako, ni bilo shranjeno nič. Poskusite znova ali se
  obrnite na razvijalca.

## Kaj je to

Tu sistemu predate en dokument — izjavo, certifikat ali navodilo za
uporabo — ki ste ga našli sami, namesto da bi ga sistem našel sam. En PDF
naenkrat.

## Kdaj to uporabite

Imate dokument — od dobavitelja, iz e-pošte ali od koderkoli — in želite,
da ga sistem prebere in poveže z vašimi izdelki. Ali pa ste kliknili
**Upload what I found** na kartici na strani **Missing documents** (ali
**Upload document** na zaslonu **Manual**), ker je sistem sam iskal
dokument in ga ni našel.

## Preden začnete

- Datoteka mora biti shranjena na vašem računalniku ali napravi.
- Biti mora PDF. Sistem sprejme datoteko le, če se njeno ime konča na
  `.pdf`.
- Manjša mora biti od 25 MB. To je današnja privzeta omejitev; razvijalec
  jo lahko dvigne. Če je datoteka zavrnjena, sporočilo na zaslonu navaja
  dejansko veljavno omejitev.
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
3. Pod **PDF file** izberite dokument z računalnika.
4. Kliknite **Upload**.

## Kaj se zgodi nato

Stran se ne osveži znova. Pod gumbom se pojavi vrstica: *Document received.
The system reads it and it appears in Review within a few minutes, or on the
item straight away if everything on it is clear.* To je vaše potrdilo.

- Vaša datoteka je trajno shranjena, ne glede na to, kaj se zgodi potem.
- Če je bila ista datoteka že prej naložena in stran ni navedla artikla,
  se ne zgodi nič več: sistem jo že ima.
- Sicer jo sistem prebere in poskusi povezati z vašimi izdelki, enako kot
  vse, kar najde sam. Če je prepričan, dokument šteje takoj. Če ni,
  počaka na vas na strani **Review**.
- Če ste prišli s kartice na strani **Missing documents** ali iz zahteve
  **Manual**, ta izgine s svojega seznama, takoj ko sistem datoteko
  prevzame.
- Ta zaslon vam ne pove, kaj od tega se je zgodilo. Če želite vedeti,
  pozneje preverite **Review** ali **Documents**.

## Kaj lahko gre narobe

| Vidite | Pomeni | Kaj storiti | Dobro ali slabo |
|---|---|---|---|
| *only PDF uploads are accepted* | Izbrana datoteka ni PDF ali se njeno ime ne konča na `.pdf` | Shranite ali izvozite dokument kot PDF in poskusite znova | Slabo — nič ni bilo shranjeno |
| *that file is named .pdf but its contents are not a PDF* | Datoteka se konča na `.pdf`, a znotraj ni PDF. Skoraj vedno gre za spletno stran, shranjeno iz brskalnika: shranila se je stran, ne dokument, na katerega stran kaže | Znova odprite stran, z desnim klikom na povezavo do dokumenta izberite **Shrani povezavo kot** in naložite tisto datoteko | Slabo, a dobro ujeto: pred septembrom 2026 se je taka datoteka vložila kot prava izjava |
| *file exceeds the 25 MB limit* | Vaša datoteka je večja, kot jo sistem trenutno dovoljuje | Prosite razvijalca za dvig omejitve ali pošljite manjšo datoteko | Slabo — nič ni bilo shranjeno |
| *Document received…* in nato nič več | Normalno. Sistem dela na tem v ozadju | Če želite vedeti, kaj se je zgodilo, pozneje preverite **Review** ali **Documents** | V redu |
| Sporočilo o napaki, ki ga ne prepoznate | Nekaj je spodletelo na strani sistema | Nič ni bilo shranjeno. Poskusite znova ali se obrnite na razvijalca | Slabo, a varno |

## Besede, ki jih uporablja zaslon

| Zaslon pravi | Pomeni |
|---|---|
| "A PDF you already have: the system reads it, and it appears in Review or on the item itself" | Vrstica pod naslovom, ki pove, kaj ta zaslon počne |
| "Uploading a document for …" | Artikel, za katerega je to nalaganje, poimenovan s kartice na strani **Missing documents**, s katere ste prišli |
| "Technical details" | Odpre številko opravila za vašim potrdilom. Uporabna le, če morate o njej vprašati razvijalca |
| "Already in progress." | To nalaganje se že bere. Vaš pritisk ni ničesar spremenil |

## Sorodno

- [Review](review.sl.md) — kje mora naloženi dokument navadno počakati na vašo odločitev
- [Missing documents](missing.sl.md): kjer je gumb **Upload what I found**
- [Manual](manual.sl.md) — od kod prihaja povezava **Upload document**
- [Documents](documents.sl.md) — kje se pojavi odobrena naložena datoteka
- [Glossary](../glossary.sl.md) — vse besede na enem mestu
