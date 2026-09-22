# Onboard a supplier

**V enem stavku:** čakalna vrsta dobaviteljev, ki jih sistem še vedno išče na
slepo, in štirje kratki koraki, ki enega uredijo.

**Stanje:** Deluje. Preverjeno glede na kodo, 15. 9. 2026.

---

## Ali lahko tu kaj pokvarim?

Skoraj nič, in nič, česar ne bi bilo mogoče razveljaviti.

- **S tujih spletnih strani se ne prenese nič**, dokler ne pritisnete
  **Probe**, ta pa prebere eno samo stran: nobenega dokumenta ne prenese,
  ničesar ne shrani in nobene nastavitve ne spremeni.
- Nič tukaj ne objavi dokumenta. Vse, kar sistem pozneje najde, še vedno čaka
  na človeka na [Review](review.sl.md).
- Vsaka nastavitev, ki jo vnesete, se shrani kot oštevilčena različica z vašim
  imenom in jo je mogoče spremeniti ali povrniti na dobaviteljevi
  [strani playbooka](playbooks.sl.md).
- **Spreminjajte previdno:** umik dobavitelja *iz čakalne vrste* je odločitev,
  na katero se bodo zanašali drugi. Če pravega proizvajalca označite kot
  "ni proizvajalec", njegove dokumentacije ne bo nihče več iskal, dokler ga
  kdo ne vrne. Zato je razlog obvezen.

---

## Kaj je to

Dobavitelj brez **playbooka** je tak, ki ga sistem išče na slepo: vsako
iskanje teče neomejeno, in sistem ne pozna nobene strani, ki bi naštevala
dokumente tega dobavitelja. Takih je večina: ob pisanju tega 328, proti 39, ki
playbook imajo.

Ta zaslon je čakalna vrsta teh dobaviteljev in delo, da se izprazni. Štiri
stvari, iz katerih je playbook sestavljen, postavi v vrstni red, vsako na svoj
zaslon, tako da vam nikoli ni treba vedeti, katera stran je naslednja.

Vrsta je urejena po številu artiklov posameznega dobavitelja, ker je tam
playbook največ vreden. Dobavitelj brez artiklov sploh ni v vrsti: playbook še
nima česa pokrivati.

---

## Kdaj ga uporabite

- Redno, kot delo, ki ga vzamete v roke in odložite. Števec v kotu, ki pada, je
  bistvo tega.
- Ko se v Business Central pojavi nov dobavitelj in se začne pojavljati brez
  dokumentacije.
- Ko kdo vpraša, zakaj sistem za nekega dobavitelja ni našel ničesar: zelo
  pogosto je odgovor, da zanj ni playbooka.

---

## Preden začnete

Nič. Vse, kar potrebujete, je na zaslonu.

Pomaga, če imate v drugem zavihku odprto dobaviteljevo spletno stran, saj vas
2. korak vpraša zanjo, 3. korak pa za njihovo stran s prenosi.

---

## Kaj storite

1. Odprite **Playbooks** v bloku **Operator** na dnu menija, nato
   **Onboard a supplier**.
2. Pritisnite **Start with …**, da vzamete dobavitelja na vrhu, ali kliknite
   katerokoli ime na seznamu.
3. **1. korak, Who.** Preberite, kaj sistem že ve: koliko artiklov ima ta
   dobavitelj, njegove šifre in koliko dokumentov hranimo. Nato playbooku
   dajte kratko ime (predlog je običajno pravi) in pritisnite
   **Start & continue**.
4. **2. korak, Their websites.** Vnesite dobaviteljeve lastne spletne naslove,
   enega na vrstico. Napaka tukaj ne stane nič: če omejeno iskanje ne najde
   ničesar, sistem poskusi znova brez omejitve. Če ne veste, pustite prazno in
   pritisnite **Continue**.
5. **3. korak, Their library.** Mnogi proizvajalci objavijo eno stran, ki
   našteva vse njihove izjave in navodila. Prilepite naslov te strani in
   pritisnite **Probe**. Sistem prebere to eno stran in vam pove, kaj je
   našel: kako se rezultat bere, piše pri [Playbooks](playbooks.sl.md). Ko je
   videti pravilno, pritisnite **Save this recipe**. Shrani **crawl recipe**:
   navodilo, kako to eno stran spremeniti v vse dokumente na njej.
6. Če take strani nimajo, pritisnite **They have no library page, continue**.
   To je običajen izid, sistem pa bo njihove dokumente še vedno iskal po enem
   artiklu naenkrat.
7. **4. korak, Done.** Preberite, kaj se bo zgodilo nato, in pritisnite
   **Take the next supplier**.

**Če ta dobavitelj sploh ne sodi v vrsto**, uporabite polje na dnu 1. koraka:
povejte, ali ni proizvajalec, nima spletne strani ali nima strani s knjižnico,
vpišite kratek razlog in pritisnite **Take out of the queue**. Brskalnik pred
tem prosi za potrditev, preden dobavitelja odstrani iz vrste.

---

## Kaj se zgodi nato

- Artikli tega dobavitelja gredo v naslednje iskanje, omejeno na spletne
  naslove, ki ste jih vnesli.
- Če ste shranili stran s knjižnico, se dokumenti z nje pridobijo vljudno — po
  ena zahteva naenkrat, časovno razmaknjeno, da tuj strežnik ni preobremenjen —
  in preberejo.
- Vse, česar sistem ni gotov, čaka človeka na [Review](review.sl.md).
  **Nič se ne objavi brez človeka.**
- Dobavitelj, ki ste ga umaknili iz vrste, je naveden na dnu strani z vrsto,
  in sicer z besedami, ki ste jih izbrali ("issues no declarations of its
  own", "has no site we can search", "has a site but no page listing
  documents"), z vašim razlogom in tem, kdo ga je dal. Gumb **Put back** ga
  vrne, izvirni zapis pa se v obeh primerih ohrani.

---

## Kaj lahko gre narobe

| You see | Pomeni | Kaj storiti | Dobro ali slabo |
|---|---|---|---|
| *… is not a usable slug* | Kratko ime vsebuje veliko črko, presledek ali simbol | Uporabite male črke, števke in posamezne vezaje | Nevtralno |
| *… already has playbook …* | Nekdo je tega dobavitelja uredil, medtem ko ste ga gledali | Odprite playbook, ki ga sporočilo imenuje, in nadaljujte tam | Nevtralno |
| Sporočilo, da so se nastavitve spremenile, medtem ko je bil vaš obrazec odprt | Nekdo drug je tega dobavitelja shranil sočasno | Znova naložite stran in ponovite spremembo | Nevtralno — nič se ne izgubi, le ponoviti je treba |
| *say why in a few words* | Dobavitelja ste poskusili umakniti iz vrste brez razloga | Napišite ga. Čez mesece sta "ne vlaga ničesar" in "dogovorili smo se, da nima česa vlagati" od zunaj videti enako | Nevtralno |
| **Refused** pri preverbi | Pravila tiste spletne strani pravijo, da je ne smemo brati samodejno | Ni kaj popraviti. Nadaljujte brez strani s knjižnico | Nevtralno — sistem upošteva pravilo, ni napaka |
| **Nothing matched** pri preverbi | Ali naslov ni stran s seznamom, ali pa stran hrani povezave tako, da jim sistem ne more slediti | Sami odprite stran in preverite, ali so dokumenti navadne povezave | Nevtralno |

---

## Besede, ki jih uporablja zaslon

| The screen says | Pomeni |
|---|---|
| **Playbook** | Vse, kar sistem ve o tem, kje iskati dokumentacijo enega dobavitelja |
| **Waiting** | Koliko dobaviteljev še nima playbooka |
| **Short name** | Ime samega playbooka in naslov njegove strani |
| **Their library** | Dobaviteljeva stran, ki *našteva* dokumente, ne dokument sam |
| **Probe** | Pogled na to stran, ki sporoči, kaj je našel, in ničesar ne spremeni |
| **Take out of the queue** | Zabeleži, da ta dobavitelj ne potrebuje playbooka, in zakaj |

---

## Sorodno

- [Playbooks](playbooks.sl.md) — celotna stran playbooka, kjer je mogoče spremeniti vse, kar je nastavljeno tukaj
- [Manufacturers](manufacturers.sl.md) — vse ostalo o enem dobavitelju
- [Review](review.sl.md) — kjer to, kar sistem najde, čaka na človeka
- [Glossary](../glossary.sl.md) — vse besede na enem mestu
