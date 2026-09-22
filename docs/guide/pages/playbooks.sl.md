# Playbooks

**V enem stavku:** kar sistem ve o enem dobavitelju: kje so njegovi dokumenti
in kako bere njegovo dokumentacijo.

**Stanje:** Deluje. Preverjeno glede na kodo, 11. 9. 2026.

---

## Ali lahko tu kaj pokvarim?

Nekatera polja tukaj je varno spreminjati sproti. Nekaj jih ni. Preberite to,
preden se dotaknete česarkoli mimo tistih, označenih kot varna.

- Nič tukaj ne izbriše dokumenta, in nič se od tod ne pošlje dobavitelju.
- Vsaka shranitev se zabeleži kot nova, oštevilčena različica. Nič se ne
  prepiše — vedno se lahko vrnete nazaj z **Restore**.
- **Varno je prosto spreminjati:** seznam domen, portalski viri dokumentov
  (strani, ki jih človek odpre ročno), besedila za datume in vrste
  dokumentov, ter vrstni red iskanja. Če eno od teh nastavite narobe, v
  najslabšem primeru sistem išče slabše — zaradi njih sam po sebi nikoli ne
  pošlje zahteve nikamor, in zaradi njih nikoli ne objavi napačnega
  dokumenta.
- **Varno, in uporabite ga najprej:** **Probe** pod *Crawl recipes*. Odpre eno
  samo stran, ki jo navedete, prešteje povezave na njej in vam pokaže vzorec.
  Ničesar ne shrani, nobenega dokumenta ne prenese in nobene nastavitve ne
  spremeni — tako preverite, ali je recept pravilen, *preden* ga shranite. Za
  to stran vseeno vpraša dobaviteljev strežnik, zato počaka, da je na vrsti,
  in odneha, če pravila tiste strani pravijo, da ne smemo pogledati.
- **Spreminjajte previdno:** shranjen **recept za pregled** (crawl recipe)
  spremeni stran, ki dokumente *našteva*, v vse dokumente na njej — zato
  presplošen recept pomeni, da sistem prenese na stotine datotek, ki niso
  dokumentacija o skladnosti. Najprej uporabite Probe, poglejte, koliko
  povezav ni prepoznanih, in vzorec ožite, dokler ne prevladajo dokumenti.
  Viri dokumentov "direct" so spet nekaj drugega — vsak
  naslov, naveden tam, sistem samodejno pridobi, ne da bi ga kdo prej
  preveril. Napačen naslov je resnična zahteva, poslana na tuj strežnik.
  Enako velja za odkljukanje polja, ki preskoči celoten zaledni arhiv starih
  dokumentov nekega dobavitelja — potreben je razlog, ker se čez mesece nihče
  več ne spomni, zakaj. Enako velja za namige za izluščanje (extraction
  hints) — to je edino besedilo v playbooku, ki ga dejansko prebere model
  umetne inteligence.
- **Tukaj sploh ni mogoče urejati:** ime dobavitelja (to je potrjeno dejanje
  na njegovi lastni [strani proizvajalca](manufacturers.sl.md)) in njegova
  druga znana imena. Eno napačno alternativno ime bi lahko potegnilo
  certifikate drugega dobavitelja na izdelke tega, zato tukaj namenoma ni
  urejevalnika zanj.
- Če je shranitev zavrnjena, se ni zapisalo nič. Popravite, kar sporočilo
  pravi, in poskusite znova, ali vprašajte razvijalca.

---

## Kaj je to

Nastavitve za posameznega dobavitelja, ki usmerjajo, kako sistem išče
dokumentacijo dobavitelja in jo bere, ko jo najde: katere spletne strani naj
preišče, katere strani ali datoteke naj pridobi, katere besede na strani
pomenijo "izjavo" ali določen datum, in kratke opombe, ki jih model umetne
inteligence prebere ob vsakem dokumentu tega dobavitelja.

Vsak playbook nižje na svoji strani ohranja zgodovino sprememb — kdo je kaj
spremenil in zakaj — ter gumb za povrnitev prejšnje različice.

---

## Kdaj to uporabite

- Nov dobavitelj je bil pravkar dodan in potrebuje playbook, preden
  lahko sistem začne iskati njegovo dokumentacijo.
- Sistem pri enem dobavitelju ves čas zgreši očiten dokument, ali napačno
  bere njegove datume ali vrste dokumentov.
- Želite videti ali povrniti prejšnjo različico nastavitev nekega
  dobavitelja.

---

## Preden začnete

Dobavitelj mora že imeti stran pod [Manufacturers](manufacturers.sl.md). Če
zanj še nihče ni zagnal playbooka, to najprej storite na dobaviteljevi lastni
strani.

Če playbook še ni bil v celoti prenesen v sistem, ta stran ga prikaže samo za
branje, z vsemi polji onemogočenimi in sporočilom o tem.

---

## Kaj storite

1. Kliknite **Playbooks** v meniju, ali ga odprite z dobaviteljeve lastne
   strani.
2. Kliknite kratko ime dobavitelja, da odprete njegove nastavitve.
3. Preverite **Identity** za znana imena in kode tega dobavitelja — teh
   tukaj ni mogoče urejati.
4. Če se pojavi, uporabite **Claim another BC code**, da temu dobavitelju
   pripnete še eno kodo Business Central. Seznam vsebuje vse kode, ki jih
   pozna Business Central in jih ne zahteva playbook drugega dobavitelja, in
   se začne z **Choose a code…**, da nobene ne izberete po nesreči; v polje
   nad njim vpišite iskani niz, da ga zožite — deluje koda ali del imena
   dobavitelja, vrstica pod poljem pa pove, koliko jih je ostalo. Če polje
   izpraznite, se spet pokaže celoten seznam. Izberite eno in pritisnite
   **Claim it**: pove vam kodo in kdo jo danes ima, če jo kdo ima, ter vas
   prosi za potrditev, preden jo premakne. Pritisnite **Yes, claim it**, da
   nadaljujete, ali **Cancel**, da se umaknete brez sprememb.
5. Pod **Official domains** naštejte dobaviteljeve lastne spletne strani, eno
   na vrstico.
6. Pod **Document sources — portals** naštejte strani, ki bi jih človek
   odprl ročno, da najde dokumente. Pod **Document sources — direct**
   naštejte natančne spletne naslove, ki naj jih sistem pridobi samodejno —
   tja postavite le naslov prave datoteke dokumenta, nikoli strani, ki
   dokumente samo našteva.
7. Odkljukajte **Skip this manufacturer's corpus folder** le, če ste se s
   kom dogovorili, da starejše dokumentacije tega dobavitelja ni treba
   pregledovati, in zapišite zakaj v polje ob njem.
8. Pod **Extraction hints** dodajte kratko opombo za posamezno polje, če
   sistem pri tem dobavitelju vztrajno napačno bere kaj določenega.
9. Pod **Date labels** in **Document-type markers** dodajte natančne besede,
   ki jih ta dobavitelj natisne za datum ali vrsto dokumenta, eno na
   vrstico.
10. Pod **Discovery ladder** odkljukajte, katere metode iskanja naj se
    preizkusijo in v katerem vrstnem redu — ali pustite vse neodkljukano, da
    se uporabi sistemov privzeti vrstni red.
11. Vnesite kratko opombo, kaj ste spremenili in zakaj, nato pritisnite
    **Save**.
12. Da spremembo razveljavite, poiščite želeno različico pod **History** in
    pritisnite **Restore**.

Da sistem naenkrat naučite celotne knjižnice prenosov nekega dobavitelja,
uporabite **Crawl recipes** blizu dna strani:

1. V polje **Library page** prilepite naslov strani, ki dobaviteljeve
   dokumente *našteva* — seznam, ne posameznega dokumenta.
2. **Link pattern** pustite na `\.pdf$`, razen če veste bolje. Ta odloča,
   katere povezave na tisti strani se štejejo.
3. **Other hosts** zaenkrat pustite prazno. Pomembno je le, kadar dobavitelj
   dokumente našteva na enem naslovu, datoteke pa streže z drugega — glejte
   korak 6.
4. Neobvezno pod **Type rules** vpišite eno pravilo `beseda=TIP` na vrstico —
   beseda je katerikoli del naslova, `TIP` pa eden od DoC, EC, IFU, ISO.
   Pravilo, za katerim za `=` ni ničesar, pomeni "štej jih, a jih nikoli ne
   vloži"; tako varnostne liste nekega dobavitelja obdržite zunaj registra.
5. Pritisnite **Probe** in počakajte. Običajno traja sekundo ali dve; stran,
   ki jo mora sistem odpreti v brskalniku, traja približno pol minute.
6. Rezultat berite od vrha: najprej, ali smo smeli pogledati, nato koliko
   povezav se je ujemalo, nato vzorec približno dvajsetih z oznako, kot kaj se
   vsaka *bere* (**reads as**). Če rezultat pove, da so povezave **off-host** —
   ujemale so se, a kažejo drugam kot na to stran — je to primer, za katerega
   je **Other hosts**: odprite enega tistih dokumentov v brskalniku, kopirajte
   del naslova med `https://` in naslednjim `/`, ga vpišite v svojo vrstico pod
   **Other hosts** in ponovite preverbo.
7. Če je vzorec presplošen — velik delež **unclassified** ali vzorec, poln
   cenikov — ga zgoraj spremenite in pritisnite **Probe again**.
8. Ko je vzorec videti pravilen, vnesite kratko opombo in pritisnite **Save
   this recipe**.

---

## Kaj se zgodi nato
- Shranitev takoj zapiše novo, oštevilčeno različico, in stran jo prikaže.
- Shranitev brez opombe je zavrnjena — opomba je tisto, kar zgodovino
  pozneje naredi uporabno.
- Če je nekdo drug shranil nastavitve tega dobavitelja, medtem ko je bila
  vaša stran odprta, je vaša shranitev zavrnjena, namesto da bi tiho
  prepisala njegovo. Znova naložite stran in ponovite spremembo.
- **Restore** prav tako ne izbriše ničesar — staro nastavitev zapiše naprej
  kot povsem novo različico, tako da tako napaka kot popravek ostaneta
  vidna v zgodovini.
- Shranitev recepta za pregled ga **shrani**; ne požene ga. Sistem ga
  uporabi, ko bo naslednjič iskal dokumente tega dobavitelja.
- Shranitev recepta za stran, za katero recept že obstaja, tistega
  **zamenja**, ne doda drugega.
- Priklic kode Business Central do potrditve ne zapiše ničesar; ko potrdite,
  začne veljati takoj. Kod s tega zaslona ni mogoče odstraniti — to je
  namenoma prepuščeno razvijalcu.

---

## Kaj lahko gre narobe

| You see | Pomeni | Kaj storiti | Dobro ali slabo |
|---|---|---|---|
| *a note is required: say what you changed and why* | Pritisnili ste Save brez vnesenega razloga | Vnesite kratek razlog in znova shranite | Nevtralno |
| A message saying this supplier's settings changed while your form was open | Nekdo drug je shranil playbook tega dobavitelja, potem ko ste odprli stran | Znova naložite stran in ponovite spremembo | Nevtralno — nič dela se ne izgubi, le ponoviti ga je treba |
| A refusal naming a web address as not one the system may fetch automatically | Ta naslov je na seznamu strani, ki jih sistem ne sme zahtevati neposredno | Prestavite ga na seznam portals namesto tega, da ga človek odpre ročno | Slabo, če se ponavlja — uporabite seznam portals |
| *This playbook is not in the database yet, so it cannot be edited* | Nihče še ni v celoti prenesel nastavitev tega dobavitelja v sistem | Vprašajte razvijalca | Nevtralno |
| A message saying a code already belongs to a different supplier's playbook | Ta koda je zahtevana drugje | Preden karkoli storite, potrdite, kateri dobavitelj dejansko izdaja to kodo | Slabo, če je narobe — najprej preiščite |
| **Refused.** … Nothing was fetched | Pravila tiste strani pravijo, da je ne smemo brati samodejno | Ni kaj popraviti — uporabite Document sources — portals, da jo človek odpre ročno | Nevtralno — to je sistem, ki upošteva pravilo, ne napaka |
| **Could not ask** | Do datoteke `robots.txt` tiste strani sploh nismo prišli, zato nismo imeli dovoljenja za naprej. Naša stran, ne zavrnitev — odpoved DNS ali prekinjena povezava | Poskusite znova. Če se ponavlja, povejte razvijalcu | Nevtralno — vredno enega ponovnega poskusa |
| **Could not read it** | Smeli smo pogledati, a stran se ni odzvala | Preverite naslov v svojem brskalniku, nato poskusite znova | Nevtralno — običajno stran, začasno |
| **Nothing matched**, čeprav so bile na strani povezave | Ali je vzorec napačen, ali pa ta knjižnica hrani naslove tako, da jim sistem ne more slediti — nekatere strani seznam zgradijo z JavaScriptom in vsak naslov postavijo v vrstico tabele namesto v povezavo | Sami odprite stran in preverite, ali so dokumenti navadne povezave, preden spremenite vzorec | Nevtralno |
| **Nothing matched** in na strani sploh ni bilo povezav | Seznam najbrž riše JavaScript, ali pa to ni stran s knjižnico | Poskusite pravo stran s knjižnico; če je prava, vprašajte razvijalca | Nevtralno |
| Velik delež **unclassified** | Vzorec lovi datoteke, ki niso dokumenti o skladnosti | Zožite vzorec ali dodajte pravila za tip | Slabo, če tako shranite — sistem bi prenesel vse |
| Število povezav, ki so **off-host** | Dobavitelj našteva na enem naslovu, datoteke pa streže z drugega — pogosto in ni napaka | Tisti drugi naslov vpišite pod **Other hosts**, enega na vrstico, in ponovite preverbo | Slabo, če prezrete — običajno so to prav dokumenti, po katere ste prišli |
| *…carries a path. An extra host is matched whole* | Pod **Other hosts** ste prilepili cel naslov dokumenta namesto samo gostitelja | Obdržite le del med `https://` in naslednjim `/`; sporočilo vam pokaže, kaj vpisati | Nevtralno — nič ni bilo shranjeno |
| Vrstica, da so bile povezave **dropped at the cap** | Knjižnica ima več ujemajočih se povezav, kot dovoljuje omejitev | Zvišajte **Max links** ali zožite vzorec | Slabo, če prezrete — dobili bi le del knjižnice |
| *This playbook is not in the database yet, so there is nothing to save into* | Preverili ste dobavitelja, čigar nastavitve še niso v celoti v sistemu | Rezultat preverbe je še vedno veljaven — prosite razvijalca, da playbook najprej uvozi | Nevtralno |

---

## Besede, ki jih uporablja zaslon

| The screen says | Pomeni |
|---|---|
| **Playbook** | Vse, kar sistem ve o tem dobavitelju |
| **Aliases** | Vsako drugo ime, pod katerim je ta dobavitelj znan. Tukaj ni urejljivo |
| **BC codes** | Kode dobavitelja v Business Central, ki jih ta playbook zahteva |
| **Revision** | Oštevilčena, datirana različica nastavitev tega dobavitelja |
| **Raw JSON** | Nastavitve točno tako, kot jih hrani sistem — za razvijalca v branje, ne za urejanje tukaj |
| **Crawl recipe** | Shranjeno navodilo, kako eno stran s seznamom dokumentov spremeniti v vse dokumente na njej |
| **Probe** | Pogled na tisto stran, ki sporoči, kaj je našel, in ničesar ne spremeni |
| **Library page** | Dobaviteljeva stran, ki našteva njegove dokumente — ne dokument sam |
| **Link pattern** | Katere povezave na tisti strani se štejejo. `\.pdf$` pomeni "naslovi, ki se končajo na .pdf" |
| **Other hosts** | Dodatni naslovi, s katerih so dokumenti lahko streženi, kadar niso na strani knjižnice same. Eden na vrstico, samo gostitelj, brez `/…` za njim |
| **Reads as** | Kot kaj povezava *izgleda* zgolj po svojem naslovu. Ugibanje, nikoli branje datoteke |
| **Unclassified** | Povezave, ki so se ujemale, a jih ni prepoznalo nobeno pravilo za tip. Vedno prikazano, nikoli skrito |

---

## Sorodno

- [Onboard a supplier](onboarding.sl.md) — vodeni način, kako napisati prvi
  playbook; povezava je na vrhu tega zaslona
- [Manufacturers](manufacturers.sl.md) — kjer nov dobavitelj dobi svoj prvi playbook
- [Documents](documents.sl.md) — kaj je sistem za tega dobavitelja dejansko že našel
- [Glossary](../glossary.sl.md) — vse besede na enem mestu
