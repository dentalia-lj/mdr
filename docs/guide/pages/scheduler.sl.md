# Scheduler

**V enem stavku:** ta stran prikazuje deset opravil, ki jih sistem izvaja po
lastnem urniku, ali je vsako od njih nedavno steklo, in vam omogoča, da tri
od njih zaženete predčasno.

**Stanje:** V živo. Preverjeno na delujoči strani 7. 9. 2026.

---

## Ali lahko tu kaj pokvarim?

Ne.

- **Run now** obstaja pri treh od desetih vrstic. Pritisk nanj to opravilo
  zažene predčasno — nič ne preskoči, prekliče ali podvoji. Če naj bi isto
  opravilo tako ali tako kmalu steklo po lastnem urniku, se oba zliata v en
  sam zagon, namesto da bi se delo opravilo dvakrat.
- Pritisk sproži pravo delo, zato ga med potekajočim zagonom ne pritiskajte
  znova in znova. En pritisk zadošča.
- Tu ni ničesar mogoče izbrisati ali razveljaviti. Če gumb vrne napako, se ni
  zgodilo nič — poskusite znova ali vprašajte razvijalca.

---

## Kaj je to

Sedem opravil teče samih od sebe po urniku, ne da bi kdo kaj kliknil. Ta stran
prikaže vseh deset: kdaj je vsako nazadnje steklo, ali teče pravočasno, ali je
še vedno **armed**, in kaj je povedalo zadnje tedensko poročilo.

**Armed** pomeni, da ima sistem še vedno živo navodilo, naj to opravilo izvaja
naprej. Opravilo, ki je večkrat zapored spodletelo, se ustavi, in njegova
vrstica bo pokazala *not armed* z gumbom **Re-arm** ob sebi. Pritisk nanj je
varen — opravilo vrne na urnik in ne stori ničesar drugega. Če hkrati več
vrstic pokaže *not armed*, je to vprašanje za razvijalca, ne za vas.

---

## Kdaj to uporabite

- Da preverite, ali je sinočnji ali tedenski samodejni zagon dejansko
  stekel.
- Da preberete zadnji tedenski povzetek, kaj poteka in kaj je že preteklo.
- Da predčasno zaženete tedensko poročilo ali preverjanje nabiralnika, brez
  čakanja na lastni urnik.

---

## Preden začnete

Nič. Dovolj je, da ste prijavljeni.

---

## Kaj storite

1. Kliknite **Scheduler** v bloku **Operator** na dnu menija.
2. Preberite stolpec **State** za vsako od desetih opravil — glejte
   razpredelnico ocen spodaj.
3. Če ima vrstica gumb **Run now** in želite, da se opravilo izvede
   predčasno, ga pritisnite.
4. Pomaknite se navzdol do **Latest weekly report** za zadnji povzetek, kaj
   poteka in kaj je že preteklo.

---

## Kaj se zgodi nato

Deset opravil, in ali ima vsako od njih delujoč gumb **Run now**:

| Opravilo | Kaj počne | Gumb **Run now**? |
|---|---|---|
| **Monthly catalogue ingest** | Uvozi najnovejši seznam izdelkov iz Business Centrala | Ne — uvoz namesto tega zaženite sami na strani [Ingest](ingest.sl.md) |
| **Expiry scan** | Preveri datume vsakega dokumenta in posodobi, kaj poteka in kaj je že preteklo. Če je vklopljeno, tudi poišče novejšo različico vsega, kar je preteklo | Ne |
| **Coverage scan** | Vsak dan poišče dokumente za manjšo skupino izdelkov, ki jih še nimajo | Ne |
| **Discovery failure monitor** | Spremlja dobavitelje, pri katerih se dokumenti nehajo najdevati, da jih lahko označi za ponoven pregled | Ne |
| **Weekly report** | Sestavi povzetek, kaj poteka in kaj je že preteklo, prikazan na dnu te strani | **Da** |
| **Mailbox poll** | Preveri nabiralnik za dobaviteljeve odgovore in priloge | **Da** |
| **EUDAMED certificate register pull** | Naenkrat prenese evropski register certifikatov in ga poveže z našimi proizvajalci | **Da** — in to je edini način, da se sploh kdaj osveži, saj je njegov lastni urnik izklopljen |
| **EUDAMED device sweep — mark due** | Označi proizvajalce, pri katerih je pregled pripomočkov na vrsti. Pregleda nikoli ne zažene sam: za vsakega dobavitelja človek pritisne **Start the check** na strani [Manufacturers](manufacturers.sl.md) | Ne |
| **Health watch** | Pošlje eno vrstico na opozorilni kanal, kadar se storitev neha javljati, se čakalna vrsta ustavi ali eno od teh opravil odmre. Enkrat na težavo, z drugo vrstico, ko se razreši | Ne |
| **Business Central — re-push what drifted** | Pošlje tri skladnostna polja nazaj v Business Central za tiste artikle, pri katerih se je odgovor spremenil, najstarejše najprej. Potrebuje dve stikali: pisanje nazaj samo in še ločeno stikalo samo za to opravilo, da lahko prvo skupno pošiljanje preverite, preden začne samo polniti Business Central. Obe sta izklopljeni, kar je običajna nastavitev | Ne |

Sedem opravil brez gumba teče znotraj sistema samega — ni ničesar ločenega,
kar bi lahko zagnali ročno. Stran to pove neposredno, z drobnim tiskom pod
vsako od teh vrstic. Za natančno besedilo glejte prevajalno razpredelnico
spodaj.

---

## Kaj lahko gre narobe

| Kar vidite | Kar to pomeni | Kaj storiti | Dobro ali slabo |
|---|---|---|---|
| **ok** | To opravilo je za trenutno obdobje že steklo | Nič | Dobro |
| **due** | Za trenutno obdobje še ni steklo, a je bil zadnji zagon dovolj nedaven, da je to normalno | Nič, razen če ostane **due** veliko dlje, kot narekuje lastni urnik | Za zdaj v redu |
| **stale** | To opravilo že dolgo ni steklo — dlje kot dva lastna cikla | Povejte razvijalcu | Razvijalčev problem |
| **never** | To opravilo nima zabeleženega niti enega zagona | Povejte razvijalcu — morda scheduler sploh ni vklopljen | Razvijalčev problem |
| **unledgered** | Samo pri *EUDAMED device sweep — mark due*. To opravilo namenoma ne vodi zapisa o zagonih, zato ni obdobja, ki bi ga bilo mogoče presojati | Nič. Namesto tega poglejte stolpec **Armed** | Normalno |
| **not armed** v stolpcu Armed | To opravilo se je ustavilo in ne bo več teklo, dokler ga ne vrnete na urnik | Pritisnite **Re-arm** ob njem. Če se ponavlja, povejte razvijalcu | Vaše za pritisk, razvijalčevo ob ponovitvi |
| Pasica *"The scheduler has never run against this database"* | Celoten scheduler ne teče | Razvijalčev problem — treba ga je zagnati | Razvijalčev problem |
| **dead jobs** na ploščici tedenskega poročila, nad nič | Nek del sistemovega lastnega dela je spodletel in obupal, glede na to poročilo | Odprite [Failed](failed.sl.md) | Razvijalčevo, če ponovni zagon tega ne odpravi |
| **already lapsed** nad nič v tedenskem poročilu | Dokumenti so prekoračili svoj datum, novejšega pa ni na voljo | Odprite [Expiry](expiry.sl.md) in izterjajte obnovitev | Vaše |

---

## Besede, ki jih uporablja zaslon
| Kar piše na strani | Kar to pomeni |
|---|---|
| **Cron** | Eno od desetih opravil, ki tečejo po lastnem urniku |
| **Cadence** | Kako pogosto naj bi opravilo teklo — mesečno, dnevno, tedensko ali vsakih nekaj ur |
| **Current period** / **Last recorded** | Interna oznaka za "ta mesec" ali "ta teden" — uporablja se za ugotavljanje, ali je opravilo za zdaj že steklo |
| **Ledger** (the code shown under each task's name) | Interno ime zapisa. Ne upoštevajte ga |
| **runs in-process — no job to enqueue** | To opravilo nima ločenega gumba, ker ni samostojno delo, ki bi ga lahko zagnali samo zase |
| **armed** / **next poll** | Sistem ima še vedno živo navodilo, naj to opravilo izvaja naprej, in čas, ko bo naslednjič preveril |
| **not armed** / **Re-arm** | To opravilo se je ustavilo. **Re-arm** ga vrne na urnik |
| **Job** column, and codes like `#412` | Interna delovna enota za zagonom, s povezavo na svojo stran s podrobnostmi. Namenjeno razvijalcu |
| Emission flags (text mentioning `SCHEDULER_…` and ON/OFF) | Stikala, ki jih lahko razvijalec preklopi, da vklopi ali izklopi del opravila. Ni nekaj, kar spreminjate tukaj |
| **expiring ahead** / **already lapsed** | Enak pomen kot na [Expiry](expiry.sl.md) |

---

## Sorodno
- [Expiry](expiry.sl.md) — celoten seznam za številkami tedenskega poročila
- [Failed](failed.sl.md) — kamor vas odpelje **dead jobs**
- [Ingest](ingest.sl.md) — ročno zaženite uvoz kataloga
- [Glossary](../glossary.sl.md) — **ok / due / stale / never**, razloženo na
  enem mestu
