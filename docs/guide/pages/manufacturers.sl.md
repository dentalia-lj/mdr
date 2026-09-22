# Manufacturers

**V enem stavku:** vaši dobavitelji, vsak na svoji strani — kako se
imenujejo, katero dokumentacijo zanje hranite, in orodja, da to ostane
urejeno.

**Stanje:** Deluje. Preverjeno glede na kodo, 15. 9. 2026.

---

## Ali lahko tu kaj pokvarim?

Večinoma ne, a dva gumba je vredno razumeti, preden ju pritisnete.

- Nič tukaj ne izbriše dokumenta.
- **Start it** (zagon playbooka) ustvari prazno izhodišče za novega
  dobavitelja. Varno, v njem še ni ničesar, kar bi lahko šlo narobe. To je
  dejanje za operaterje, zato ga vidite le, če je vaša prijava operaterska.
- **Save contacts** zapiše le, komu bodo naslovljena prihodnja e-poštna
  sporočila za obnovitev. Sam po sebi ne pošlje ničesar, in kadar koli ga
  lahko znova spremenite.
- **Check what a rename would change** sam po sebi ne zapiše ničesar — le
  pokaže, kaj bi se zgodilo. Drugi gumb, **Yes, rename it**, je tisti, ki ime
  dejansko spremeni. Tudi takrat se staro ime ohrani kot drugo ime, namesto
  da bi bilo izbrisano, tako da dokumenti, ki ga že natisnejo, še vedno
  razrešijo pravilno, in ime lahko po potrebi povrnete nazaj. Če si po branju
  premislite, **Cancel** vprašanje umakne brez zapisa česar koli.
- **Confirm** in **Reject** na čakalni vrsti **EUDAMED ID (SRN) queue**
  odločata, ali en EUDAMED-ov vpis pripada enemu od vaših dobaviteljev. Oba pred dejanjem
  vprašata za potrditev v brskalniku. Nobeden od njiju na tem zaslonu nima
  gumba za razveljavitev. **Confirm** naredi le, da so certifikati tega vpisa
  na voljo za primerjavo — nič se ne vloži samodejno. **Reject** je mišljen
  kot dokončen: isti seznam se preveri znova vsak mesec, zato bi odločitev,
  ki bi se tiho razveljavila, pomenila, da te čakalne vrste ni mogoče nikoli
  izprazniti.
- **Start the check** na strani **Check due** sproži pravo preverjanje proti
  evropski bazi podatkov, potem ko vas brskalnik vpraša za potrditev.
  Nobenega vašega dokumenta se ne dotakne — le primerja, kaj je ta baza
  registrirala, s tem, kar že hranite.
- Če gumb vrne napako, se ni zgodilo nič. Poskusite znova ali vprašajte
  razvijalca.

---

## Kaj je to

Štiri povezani zasloni.

- **Manufacturers** — vsak dobavitelj, s katerim poslujete, vsak v svoji
  vrstici, s tem, koliko izdelkov in dokumentov zanj hranite, in ali ima
  sistem zanj že **playbook**.
- **Stran enega proizvajalca** — vse o enem dobavitelju: vsako ime in kodo,
  pod katero je znan, kolikšen del njegovega kataloga ima dokumentacijo, ki
  jo potrebuje, njegove dokumente, njegove izdelke in — kjer ga EUDAMED
  pozna — primerjavo z lastnimi zapisi te baze.
- **EUDAMED ID (SRN) queue** — kratek delovni seznam za potrditev, kateri
  EUDAMED-ov vpis pripada kateremu od vaših dobaviteljev.
- **Check due** — dobavitelji, ki so na vrsti za nov pregled proti EUDAMED,
  in čakajo, da ga sprožite.

---

## Kdaj to uporabite

- Želite na enem mestu videti vse, kar hranite za enega dobavitelja.
- Novega dobavitelja je treba dodati, preden lahko sistem začne iskati
  njegovo dokumentacijo.
- Ime dobavitelja na certifikatu se ne ujema s tem, kako ga imenujete vi, in
  ga je treba popraviti.
- Sistemu morate povedati, komu naj naslovi zahtevo za obnovitev.
- Vaša čakalna vrsta **EUDAMED ID (SRN) queue** ali število zapadlih
  preverjanj ni na nič.

---

## Preden začnete

Za brskanje nič. Za preimenovanje dobavitelja, dodajanje kontaktov zanj ali
zagon playbooka zanj mora ta dobavitelj že obstajati v sistemovem lastnem
seznamu dobaviteljev — pri povsem novem dobavitelju se to včasih še ne zgodi,
in stran to jasno pove, kadar je tako.

---

## Kaj storite

**Seznam Manufacturers**

1. Kliknite **Manufacturers** v meniju na levi.
2. Iščite po imenu ali kodi Business Central, ali odkljukajte
   **Only without a playbook**, da vidite dobavitelje, za katere še nihče ni
   napisal playbooka. To vam da seznam; če jih želite dejansko
   obdelati, sledite povezavi na [Onboard a supplier](onboarding.sl.md), ki iste
   dobavitelje razvrsti po tem, koliko artiklov vsak od njih pušča nepokritih,
   in si zapomni tiste, ki ste jih že izločili.
3. Kliknite ime dobavitelja, da odprete njegovo stran.

**Stran enega proizvajalca**

4. Preberite **Names in Business Central** za vsako ime in kodo, pod katero je
   ta dobavitelj znan. Od kod izvira posamezen zapis, je pod **Technical
   details** pod tem.
5. Preberite povzetek skladnosti za to, kolikšen del kataloga tega
   dobavitelja ima, kar potrebuje, in kaj manjka.
6. Pod **Find missing documents** piše, koliko skupin izdelkov tega
   dobavitelja nima nobenega potrjenega dokumenta. Pritisnite **Search for
   documents** in sistem jih začne iskati na spletu. Če ima dobavitelj več
   skupin, kot jih zajame en pritisk, vam zaslon to pove — pritisnite znova in
   nadaljujte s preostalimi. Vsak pritisk se začne pri skupinah, ki jih nihče
   ni pregledal najdlje, zato ponavljanje pritiskov predela celotnega
   dobavitelja in ne vedno istih skupin.
7. Uporabite zavihke — **All documents**, **Published documents**,
   **Items** — za brskanje po dokumentaciji in izdelkih tega dobavitelja.
8. Če za tega dobavitelja še ni nič avtorsko pripravljeno in ste operater,
   vnesite kratek naslov pod **Start a playbook** in pritisnite **Start it**.
   Pisarniška prijava namesto tega vidi vrstico, da je to za operaterje.
9. Pod **Renewal contacts** vnesite enega ali več e-poštnih naslovov,
   ločenih z vejico ali podpičjem, in pritisnite **Save contacts**.
10. Da popravite zapis imena dobavitelja, vnesite novo ime in razlog pod
   **Rename**, nato pritisnite **Check what a rename would change**.
   Preberite, kaj sporoča, in pritisnite **Yes, rename it** šele, ko ste
   prepričani, ali **Cancel**, da se umaknete brez sprememb.

**EUDAMED ID (SRN) queue**

11. Odprite [EUDAMED checks](eudamed.sl.md) in sledite povezavi
    **EUDAMED ID (SRN) queue**.
12. Za vsako vrstico preverite, ali EUDAMED-ov vpis dejansko je ta
    dobavitelj, nato pritisnite **Confirm** ali **Reject**.

**Check due**

13. Odprite [EUDAMED checks](eudamed.sl.md) in sledite povezavi **Check due**.
14. Za vsakega dobavitelja, ki je na vrsti za pregled, pritisnite
    **Start the check**. Gumb je na voljo šele, ko je EUDAMED-ova identiteta
    dobavitelja potrjena v **EUDAMED ID (SRN) queue**.

---

## Kaj se zgodi nato
- Zagon playbooka takoj ustvari praznega. Vi ali razvijalec lahko podrobnosti
  izpolnite pozneje na [Playbooks](playbooks.sl.md).
- Shranitev kontaktov začne veljati takoj — naslednja zahteva za obnovitev,
  ki jo sistem napiše, bo naslovljena nanje.
- **Check what a rename would change** ne zapiše ničesar. Pove vam, koliko
  skupin izdelkov še vedno nosi staro ime in jih ni mogoče samodejno
  premakniti, in ali kateri od njih že ima dokumente. Ime spremeni šele
  **Yes, rename it**, in to začne veljati takoj. Potrditveno sporočilo lahko
  omeni nadaljnji korak, ki ga mora še opraviti razvijalec — če je tako, mu
  to sporočite.
- **Confirm** v **EUDAMED ID (SRN) queue** naredi, da so certifikati tega
  EUDAMED-ovega vpisa na voljo za primerjavo s katalogom tega dobavitelja.
  **Reject** zabeleži, da ne gre za istega dobavitelja.
- **Start the check** v ozadju sproži pravo preverjanje proti EUDAMED. Vrstica
  zapusti seznam **Check due** takoj, ko ga sprožite, ne šele, ko je
  preverjanje dejansko končano.
- **Draft request for these N group(s)** se pojavi pod razdelkom "Groups to
  request", ko je dobavitelj preverjen proti EUDAMED in kaj manjka. En pritisk
  napiše EN osnutek, ki pokriva vse naštete skupine, in ne enega e-poštnega
  sporočila na skupino — dobavitelj, ki mu manjka sedemdeset izjav, je še vedno
  eno pismo. Vsaka vrstica navede skupino in nekaj vaših lastnih šifer artiklov
  kot primere, da dobavitelj prepozna blago, ne da bi moral iskati oznako.
  Gumb ne naredi nič, če je seznam prazen. Nič se ne pošlje: osnutek počaka na
  zaslonu **Renewal emails**, da ga preberete, naslovite in pošljete.
- **Ask for these N certificate(s)** se pojavi pod razdelkom "Certificates we
  hold no copy of". Gre za isto vrsto pisma, le močnejše: evropska baza
  poimenuje certifikat, ki ga dobavitelj ima — njegovo številko, revizijo,
  kdaj je bil izdan in do kdaj velja ter kateri priglašeni organ ga je izdal —
  pismo pa vse to navede nazaj. Dobavitelju ni treba ugotavljati, kateri
  dokument mislite, pismo pa pove, da je seznam iz njegovega lastnega vpisa,
  zato se bere kot primerjava in ne kot zahteva. En pritisk napiše EN osnutek,
  ki pokriva vse naštete certifikate. Gumb ne naredi nič, če je seznam prazen,
  in vpraša samo enkrat na dobavitelja: če osnutek že obstaja, stran to pove
  in nanj poveže, namesto da bi napisala drugo pismo. Gumb je samo na strani
  posameznega dobavitelja — zaslon **Expiry** prikaže isti seznam za vse
  dobavitelje hkrati in tam namenoma nima gumba, saj bi en pritisk pomenil
  pismo vsakemu od njih.

---

## Kaj lahko gre narobe

| You see | Pomeni | Kaj storiti | Dobro ali slabo |
|---|---|---|---|
| A message saying this supplier has no record set up yet, on Rename or Renewal contacts | Za tega dobavitelja začetna nastavitev še ni bila izvedena | Povejte razvijalcu | Nevtralno — normalno za povsem novega dobavitelja |
| A message after **Check what a rename would change**, listing product families that "will keep" the old name | Nekatere izdelke tega dobavitelja so že razvrstili pod staro ime in jih preimenovanje samo po sebi ne more premakniti | Preberite, preden potrdite. Če piše, da so dokumenti že pripeti, mora premik dokončati razvijalec | Odvisno — preberite pozorno, preden potrdite |
| *… contains the BC brand … which this manufacturer does not claim* | Novo ime se prekriva z blagovno znamko Business Central, ki pripada drugemu dobavitelju | Izberite drugo ime, ali prosite razvijalca, naj najprej uredi kodo | Slabo — ne vsiljujte |
| *… already belongs to …* | Drug dobavitelj že ima natanko to ime | Izberite drugo ime | Slabo |
| The **Start the check** button is greyed out | Nihče še ni potrdil EUDAMED-ove identitete za tega dobavitelja | Najprej predelajte **EUDAMED ID (SRN) queue** | Nevtralno |
| *Nothing waiting* on the EUDAMED ID (SRN) queue | Vsaka negotova identiteta EUDAMED je že odločena | Nič za storiti | Dobro |
| *Nothing due right now* on Check due | Naslednji pregled vsakega dobavitelja ali še ni zapadel, ali je že sprožen | Nič za storiti | Dobro |

---

## Besede, ki jih uporablja zaslon

| The screen says | Pomeni |
|---|---|
| **Playbook** | Kar sistem ve o enem dobavitelju: njihova spletna stran, kje živi njihova dokumentacija, kako je napisana |
| **Short name** | Naslov, na katerem živi playbook, z malimi črkami in števkami — `ivoclar`, ne celotno pravno ime |
| **Names in Business Central** | Vse kode in zapisi imen, ki jih Business Central hrani za tega dobavitelja |
| **Name in EUDAMED** | Ime, ki ga ta evropska baza podatkov ima zabeleženo za ta vpis |
| **EUDAMED ID (SRN)** | Ena sama identifikacijska številka dobavitelja v EUDAMED. Glejte [EUDAMED ID (SRN)](../glossary.sl.md#eudamed-id-srn) |
| **How close the names are** | Kako natančno se EUDAMED-ovo ime ujema z imenom vašega dobavitelja. Raje sami preberite obe imeni, kot da zaupate samo številki |
| **EUDAMED check** | Preverjanje enega dobavitelja proti EUDAMED |
| **Start the check** | Sproži eno preverjanje, za enega dobavitelja |
| "Attributed by what each document says about itself" | Dokumenti, ujemani s tem dobaviteljem po lastnem besedilu, ne po povezavi z izdelkom |
| The small coloured label next to a document (*Published*, *Waiting for review*, *On file*, *Replaced*, *Rejected*) | Glejte [status dokumenta](../glossary.sl.md) |
| **Technical details** | Shranjeni ključi za zaslonom — od kod izvira posamezen zapis imena, kako je bil najden kandidat v EUDAMED. Pri vsakdanjem delu jih lahko prezrete |

---

## Sorodno

- [Playbooks](playbooks.sl.md) — pisanje playbooka, na katerega kaže stran dobavitelja
- [Documents](documents.sl.md) — dokumentacija enega dobavitelja, iskana čez vse dobavitelje naenkrat
- [Items](items.sl.md) — en izdelek naenkrat
- [Expiry](expiry.sl.md) — ista primerjava z EUDAMED, za vse dobavitelje naenkrat
- [Glossary](../glossary.sl.md) — vse besede na enem mestu
