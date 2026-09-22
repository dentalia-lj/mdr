# Failed

**V eni povedi:** delo, ki ga je sistem večkrat poskusil in ga ni mogel
dokončati, razvrščeno po tem, kaj lahko storite glede tega.

**Stanje:** V uporabi. Preverjeno v kodi 11. 9. 2026.

---

## Ali lahko tu kaj pokvarim?

Ne.

- Tu se nič ne izbriše. Spodletelo opravilo ostane zabeleženo, tudi ko je
  urejeno.
- **Try the N again** in **Search again for these N** samo prosita sistem, naj
  delo opravi znova. Noben dokument se s tem ne spremeni.
- Dvakratni pritisk na gumb ne škodi. Delo, ki že čaka v čakalni vrsti, se ne
  doda še enkrat.
- Če gumb vrne napako, se ni zgodilo nič. Poskusite znova ali vprašajte
  razvijalca.

---

## Kaj je to
Seznam opravil, ki jih je sistem poskušal znova in znova, a jih ni mogel
dokončati, v treh razdelkih:

| Razdelek | Kaj je šlo narobe | Kaj lahko storite |
|---|---|---|
| **Websites that took too long** | Dobaviteljeva spletna stran ni odgovorila pravočasno | Poskusite znova. Običajno uspe |
| **Addresses that no longer work** | Spletnega naslova, ki ga je sistem imel za dokument, ni več: stran ne obstaja več ali pa spletnega mesta ni mogoče najti | Poiščite znova. Sistem poišče nov naslov dokumenta |
| **For the developer** | V samem sistemu je treba nekaj popraviti | Nič. Ponoven poskus ne bo pomagal, dokler vzrok ni odpravljen |

Vsak naslov razdelka pove, koliko opravil še potrebuje nekoga. Opravilo, ki ste
ga že poslali nazaj, se ne šteje več in je prikazano kot **trying again** ali
**searching again**, dokler se to delo ne konča.

## Kdaj to uporabite
Odprite **Failed** kot tretji korak vašega dnevnega kroga, po Review in
Missing documents.

## Preden začnete

Nič. Ta zaslon ne zahteva priprave.

## Kaj storite

1. Kliknite **Failed tasks** v bloku **Operator** na dnu menija.
2. Pod **Websites that took too long** pritisnite **Try the N again**. Gumb
   navede, koliko opravil zajema. Brskalnik vas prosi za potrditev; pritisnite
   **OK**.
3. Pod **Addresses that no longer work** pritisnite **Search again for these
   N**. Tudi potrditev pove, koliko iskanj bo sprožila. Pritisnite **OK**.
4. Razdelka **For the developer** se ne dotikajte. V njem ni gumbov za vas. Če
   pod njegovim **Show technical details** vendarle vidite gumbe **Re-run**, je
   vaša prijava nastavljena kot operaterska. Za dnevni krog jih ne potrebujete.
5. Preverite pozneje. Opravilo, ki je uspelo, izgine. Opravilo, ki znova
   spodleti, se vrne v svoj razdelek.

## Kaj se zgodi nato

- Po **Try the N again** gredo opravila takoj nazaj v čakalno vrsto. Število v
  razdelku pade na nič, stran pa jih prikaže kot **trying again**. Stečejo pred
  rednim delom v ozadju.
- Po **Search again** sistem znova poišče dokumente za vsak artikel, na enak
  način kot prvič. Kar najde, gre skozi običajna preverjanja, zato se lahko
  pojavi v Review. Če ne najde ničesar, se artikel pojavi na strani
  [Missing documents](missing.sl.md).
- Opravilo v razdelku **For the developer** čaka na popravek sistema. Navedeno
  je tudi v Manual, kot opravilo, ki je dokončno spodletelo.

## Kaj lahko gre narobe

| Vidite | Pomeni | Kaj storiti | Dobro ali slabo |
|---|---|---|---|
| Vsi razdelki kažejo (0) | Nič ne čaka na nikogar | Ni ničesar za storiti | Dobro |
| **trying again** ali **searching again** pod razdelkom | Delo, ki ste ga poslali nazaj, še teče | Nič. Preverite pozneje | Običajno |
| Spletna stran se po ponovnem poskusu vrne pod **Websites that took too long** | Stran je še vedno počasna ali nedosegljiva | Poskusite še enkrat naslednji dan. Če se ponavlja, povejte razvijalcu in navedite spletno stran | Potreben je razvijalec, če se ponavlja |
| Naslov se po iskanju vrne pod **Addresses that no longer work** | Iskanje je znova našlo isti mrtev naslov | Povejte razvijalcu in navedite spletno stran | Potreben je razvijalec |
| *"…not linked to an item, so there is nothing to search for"* | Spodleteli naslov ne pripada nobenemu artiklu, ki ga sistem pozna, zato zanj ni gumba **Search again** | Povejte razvijalcu | Potreben je razvijalec |
| *"Already in progress"* ali *"Nothing new queued"* po pritisku na gumb | To delo že čaka v čakalni vrsti ali že teče. Nič ni bilo dodano dvakrat | Nič. Preverite pozneje | Običajno |
| Sporočilo o napaki po pritisku na gumb | Ni se zgodilo nič | Poskusite znova ali vprašajte razvijalca | Poskusite znova |

## Besede, ki jih uporablja zaslon

| Zaslon pravi | Pomeni |
|---|---|
| **Page no longer exists** | Spletno mesto obstaja, strani z dokumentom pa ni več. Spletno mesto je odgovorilo »404« ali »410« |
| **Website not found** | Imena spletnega mesta ni več, zato se ni mogoče povezati |
| Imena pod naslovom razdelka (na primer `device.report`) | Spletna mesta, ki so vpletena, najpogostejša najprej. Po štirih so preostala prešteta kot »+N more« |
| **trying again** | Opravila, ki ste jih poslali nazaj in še tečejo. Ne štejejo se, dokler se ne končajo |
| **searching again** | Naslovi, za katerih artikel sistem znova išče |
| **Show technical details** | Posamezna opravila in njihova sporočila o napakah, za razvijalca. Odprite ga le, če vas kdo prosi za to |
| **Re-run all…** in **Re-run**, v tehničnih podrobnostih razdelka za razvijalca | Operaterski gumbi. Vidijo jih le operaterske prijave in samo te jih lahko uporabijo. **Try the N again** in **Search again** zgoraj sta vaša |
| *"…interactive priority"* | Ta ponovni poskus steče pred rednim delom v ozadju, zato se izvede prej |

## Sorodno

- [Missing documents](missing.sl.md): druga čakalna vrsta v vašem dnevnem krogu
- [Review](review.sl.md) — prva, in tista, ki je najpomembnejša
- [Vaš dnevni krog](../01-daily-work.sl.md) — kam se Failed umešča v vaše jutro
- [Slovar](../glossary.sl.md) — vse besede na enem mestu, vključno s kodami na tem zaslonu
