# Import from Business Central

**V enem stavku:** tu uvozite seznam izdelkov in šifrant dobaviteljev iz
Business Centrala, pri čemer se nič ne zapiše, dokler tega ne potrdite.

## Stanje

Deluje. Preverjeno s kodo 15. 9. 2026.

## Ali lahko tu kaj pokvarim?

Ne. Ta zaslon je zgrajen tako, da po nesreči ne morete zapisati ničesar.

- Noben od obrazcev na tem zaslonu ne zapiše niti ene vrstice, dokler ne
  pritisnete **Apply** na njegovem predogledu. Izbira datoteke in pritisk
  na **Upload and preview** vam le pokaže, kaj bi se zgodilo.
- Izdelki ali šifre dobaviteljev, ki jih v datoteki ni, se nikoli ne
  izbrišejo ali ukinejo. Datoteko, skrajšano na samo spremenjene vrstice,
  je varno naložiti.
- Preimenovanje dobavitelja zahteva drugo, namerno kljukico, preden je
  dovoljeno — glejte **Kaj se zgodi nato** spodaj. Brez te kljukice se ne
  zapiše čisto nič, niti nove šifre.
- Če predogled ali Apply spodleti, se ne zapiše nič. Vaša datoteka ostane
  naložena, tako da lahko poskusite znova.
- Potrditev, potem ko je isto datoteko že uvozil nekdo drug, je varna —
  zaslon vam sporoči, da so se števci premaknili, namesto da bi vas pustil
  v negotovosti.

## Kaj je to

Tu uvozite dve datoteki, ki ju izvozi Business Central: vaš seznam
izdelkov (**Artikli**) in vaš šifrant dobaviteljev (**Proizvajalci**).
Vsaka vam natančno pokaže, kaj se bo spremenilo, preden je karkoli
zapisano.

## Kdaj to uporabite

Kadar koli se vaš seznam izdelkov ali šifrant dobaviteljev v Business
Centralu spremeni in želite, da se sistemova kopija posodobi. Ne
potrebujete cele datoteke — deluje tudi izvoz, skrajšan na samo
spremenjene vrstice.

## Preden začnete

- Izvozno datoteko morate imeti shranjeno na računalniku: `.xlsx`,
  `.xlsm`, `.xls` ali `.csv`, manjšo od 25 MB. To je današnja privzeta
  omejitev; razvijalec jo lahko dvigne.
- Vedite, katerega od obeh nalagate. Seznam izdelkov in šifrant
  dobaviteljev sta dva ločena obrazca na tem enem zaslonu, vsak s svojim
  gumbom **Upload and preview**.
- Če niste prepričani, zakaj se je ime šifre dobavitelja spremenilo,
  preverite pri osebi, ki upravlja vaše podatke v Business Centralu,
  preden označite polje za preimenovanje.
- Obstaja drug, podobno poimenovan zaslon, **Ingest**, ki počne nekaj
  podobnega, a zapiše takoj, brez predogleda. Za datoteko na vašem
  lastnem računalniku uporabite **Import** — ta zaslon.

## Kaj storite

1. Kliknite **Import from Business Central** v meniju na levi.
2. Pod **Item catalogue** izberite izvoz svojih izdelkov in kliknite
   njegov **Upload and preview**. Ali pa pod **Manufacturer master**
   izberite izvoz svojih dobaviteljev in kliknite **Upload and preview**
   tega obrazca.
3. Počakajte. Predogled je naslovljen z imenom vaše datoteke in pravi
   "Reading the export…" (ali "Reading the manufacturer master…"), dokler ni
   pripravljen. Sam se preverja vsakih nekaj sekund, zato strani ni treba
   osveževati.
4. Preberite števce. Kaj kateri pomeni, glejte spodaj pod **Kaj se zgodi
   nato**.
5. Če vam polje ponudi preusmeritev nekaterih šifer, ga pred označitvijo
   natančno preberite — glejte **Kaj se zgodi nato**.
6. Kliknite **Apply**. Natančno besedilo pove, koliko vrstic bo
   zapisanih.

## Kaj se zgodi nato

Predogled seznama izdelkov pokaže te števce:

| Vidite | Pomeni |
|---|---|
| Rows in file | Koliko vrstic izdelkov je vsebovala datoteka |
| New or changed | Vrstice, ki jih bo sistem dodal ali posodobil, ko pritisnete Apply |
| Unchanged | Vrstice, ki so popolnoma enake temu, kar sistem že ima |
| Not a medical device | Vrstice, označene kot ne-medicinski pripomoček, izločene iz dela na medicinskih pripomočkih |
| Device class missing | Vrstice, pri katerih datoteka ni navedla razreda pripomočka za izdelek. Kljub temu uvožene, le označene |
| No manufacturer's article no. | Vrstice brez dobaviteljeve številke artikla. Kljub temu uvožene, le označene: poznejše ujemanje dokumentov z njimi je težje |
| Skipped | Vrstice, preveč nepopolne, da bi jih sploh shranili (brez številke artikla ali brez imena) |

Predogled šifranta dobaviteljev pokaže druge števce:

| Vidite | Pomeni |
|---|---|
| Rows in master | Koliko vrstic dobaviteljev je vsebovala datoteka |
| New codes | Šifre dobaviteljev, ki se pojavijo prvič |
| Renamed | Šifre, katerih ime se je spremenilo od zadnjič |
| Gone from the file | Šifre, ki so bile prej v vašem šifrantu dobaviteljev, v tej datoteki pa jih ni. Ohranjene točno takšne, kot so — nikoli izbrisane |
| Unchanged | Šifre, pri katerih se datoteka že ujema s sistemom |

**O preimenovanjih.** "Renamed" šifra je šifra dobavitelja, katere ime v
datoteki se razlikuje od imena, ki ga sistem že ima. Označitev polja, ki
to dovoli, vaših obstoječih izdelkov ne premakne na novo ime — ostanejo
povezani s starim. To lahko enega dobavitelja v vaših zapisih razcepi na
dva, brez samodejnega načina, da to razveljavite. Polje označite šele, ko
veste, kateri izdelki so prizadeti.

Pritisnite **Apply** in števci se resnično zapišejo. Nato se prikaže
drugi stolpec, ki pokaže, kaj je bilo dejansko zapisano. Vsako odstopanje
med predogledom in zapisom je vidno, ne skrito.

Po uvozu dobaviteljev, ki je dodal ali preimenoval šifre, lahko zaslon
prikaže kratek odstavek o izvedbi ukaza. To besedilo je namenjeno
razvijalcu, ne vam — omenite ga tistemu, ki vzdržuje sistem, namesto da
bi ga izvedli sami.

Na dnu te strani je tudi seznam **Recent runs**. Prikazuje vsako nedavno
opravilo v celotnem sistemu, ne le vaše uvoze: vsaka vrstica je datum, kaj je
bilo ("Item catalogue import", "Manufacturer master import" ali "Background
task" za vse drugo) in kako se je končalo. Odprite vrstico in nato njene
**Technical details** za surovo poročilo.

## Kaj lahko gre narobe

### Običajno — nadaljujte

| Vidite | Pomeni | Kaj storiti | Dobro ali slabo |
|---|---|---|---|
| *only BC export files are accepted (.xlsx, .xlsm, .xls, .csv) — got '…'* | Datoteka ni ene od sprejetih vrst izvoza | Izberite pravilen izvoz iz Business Centrala | Slabo — nič ni bilo naloženo |
| *file exceeds the 25 MB limit* | Vaša datoteka je večja, kot jo sistem trenutno dovoljuje | Prosite razvijalca ali skrajšajte datoteko na samo spremenjene vrstice | Slabo — nič ni bilo naloženo |
| "Reading the export…" / "Reading the manufacturer master…" | Normalno — sistem bere vašo datoteko | Počakajte trenutek; osveži se samodejno | V redu |
| Razpredelnica s števci in gumb **Apply** | Normalno — to je predogled | Preberite števce in pritisnite Apply, če so videti pravilni | V redu, še ni zapisano |
| "Applied. N item(s) written…" / "Applied. N new code(s)…" | Uspeh | Nič — številke so vaše potrdilo | Dobro |

### Poglejte natančneje, preden nadaljujete

| Vidite | Pomeni | Kaj storiti | Dobro ali slabo |
|---|---|---|---|
| Polje: *Re-point N code(s) to a different manufacturer…* | Nekaterim šifram dobaviteljev se je od zadnjega uvoza spremenilo ime | Preverite, katere šifre in ali kateri od vaših izdelkov že uporablja staro ime, preden označite | Poglejte natančneje |
| *N count(s) moved since the preview* | Vaš katalog se je spremenil med predogledom in pritiskom na Apply — verjetno je nekdo drug medtem uvažal | Preverite nove številke; če niste prepričani, znova odprite predogled | Poglejte natančneje |

### Zavrnjeno ali spodletelo — nič ni bilo zapisano

| Vidite | Pomeni | Kaj storiti | Dobro ali slabo |
|---|---|---|---|
| *The preview failed.* / *The import failed.* s tehničnim sporočilom | Nekaj je šlo narobe pri branju datoteke | Nič ni bilo zapisano. Preverite datoteko ali se obrnite na razvijalca | Slabo, a varno |
| *Refused — nothing was written.* | Datoteka je vsebovala preimenovanje dobavitelja, kljukica pa ni bila označena | Pojdite nazaj na **Import**, znova odprite predogled, kljukico označite le, če ste prepričani, nato znova pritisnite Apply | Slabo, a varno |
| *Already in progress.* | Vi ali nekdo drug ste to točno nalaganje že sprožili in še vedno teče | Počakajte, da se konča; ne pošiljajte znova | V redu |

## Besede, ki jih uporablja zaslon

| Zaslon pravi | Pomeni |
|---|---|
| "Technical details" na obrazcu za nalaganje | Oznaka kataloga, v katerega ta uvoz piše, in prioriteta v vrsti. Nobeno ni vprašanje za vas: obstaja en Business Central in eno oštevilčenje artiklov, uvoz s tega zaslona pa vedno nekdo čaka |
| **interactive** / **delta** / **sweep** (Queue priority) | Kako hitro pride to na vrsto v primerjavi z drugim čakajočim delom. **interactive** je na vrsti prva in je tisto, kar ta zaslon pošlje, če razvijalec ne spremeni drugače |
| "abandoned upload(s) collected" | Stari predogledi, ki jih nihče ni potrdil, samodejno počiščeni — ni povezano z vašo lastno datoteko |
| dedupe key | Številka potrdila za točno to zahtevo |

## Sorodno

- [Ingest](ingest.sl.md) — druga pot noter, brez predogleda; večinoma za razvijalce
- [Manufacturers](manufacturers.sl.md) — kje imena dobaviteljev živijo iz dneva v dan
- [Items](items.sl.md) — vaš seznam izdelkov
- [Glossary](../glossary.sl.md) — vse besede na enem mestu
