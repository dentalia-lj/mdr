# Renewal emails

**V eni povedi:** e-poštna sporočila za zahtevek za obnovo, ki jih je sistem
napisal namesto vas — in ki jih morate poslati sami.

**Stanje:** Delno v uporabi. Branje, urejanje in označevanje statusa osnutka
danes vse deluje. Ali se tu samodejno pojavljajo novi osnutki, je privzeto
izklopljeno, zato lahko ta seznam prikazuje le tisto, kar že obstaja, ali
povsem nič, dokler tega razvijalec ne vklopi. Preverjeno v kodi 15. 9. 2026.

---

## Ali lahko tu kaj pokvarim?

Ne, in eno dejstvo je pomembnejše od katerega koli gumba na tem zaslonu:
**sistem nikoli ne pošlje e-pošte.** Vsak gumb tukaj spremeni le oznako:
Draft, Ready to send, Sent, Archived. Noben od njih ne postavi sporočila v
nikogaršnji predal razen v vašega, in šele potem, ko ga pošljete sami.

- **Save edit** spremeni le besedilo, shranjeno tukaj.
- **Mark ready to send** samo zabeleži, da ste besedilo odobrili. Ničesar ne
  pošlje.
- **I have sent this** samo zabeleži, da ste sporočilo že poslali ročno. Tudi
  ta pritisk ničesar ne pošlje.
- **Archive this draft** osnutek obdrži, označen kot arhiviran. Nič se ne
  izbriše.
- Če gumb vrne napako, se ni zgodilo nič. Poskusite znova ali vprašajte
  razvijalca.

---

## Kaj je to
Osnutek je zahtevek za obnovo, ki ga je sistem napisal v vašem imenu in
prosi dobavitelja za dokumentacijo, ki ji kmalu poteče veljavnost, ali ki je
ni bilo mogoče najti na noben drug način. **Pošljete ga vi. Sistem tega
nikoli ne naredi.**

## Kdaj to uporabite
Odprite **Renewal emails** enkrat tedensko, po pregledu [Expiry](expiry.sl.md).
Preverite ga tudi vsakič, ko vam [Manual](manual.sl.md) pove, da je sistem
opustil iskanje dobaviteljeve dokumentacije — tudi to lahko ustvari osnutek
tukaj.

## Preden začnete

Za branje osnutka nič. Da ga dejansko pošljete, potrebujete svoj lasten
e-poštni račun — osnutek predlaga, komu pisati in kaj napisati, pošiljanje
pa poteka zunaj tega sistema, iz vašega lastnega predala.

## Kaj storite

1. Kliknite **Renewal emails** v meniju na levi ali **Open drafts** na zaslonu
   [Today](today.sl.md).
2. Neobvezno: kliknite eno od števil na vrhu (Draft, Ready to send, Sent,
   Archived), da vidite le tisto skupino.
3. Kliknite zadevo, da odprete eno sporočilo.
4. Preverite polja **To**, **Subject** in **Body**. Uredite katero koli od
   njih, dokler je sporočilo še označeno kot **Draft**: vsa tri ostanejo
   urejevalna do takrat.
5. Če je polje **To** prazno, ga izpolnite sami. Sistem nima vedno na voljo
   naslova za dobavitelja.
6. Pritisnite **Save edit**, da shranite spremembe.
7. Ko je besedilo pravilno, pritisnite **Mark ready to send**.
8. Besedilo prekopirajte v svoj lasten e-poštni program in ga pošljite na
   naslov, ki ste ga preverili v koraku 5.
9. Vrnite se sem in pritisnite **I have sent this**.
10. Če se odločite, da tega dobavitelja ne boste priganjali, namesto tega
    kadar koli pred pošiljanjem pritisnite **Archive this draft**.

## Kaj se zgodi nato

- **Save edit** posodobi le shranjeno besedilo. Nič se ne pošlje, sporočilo
  pa ostane označeno kot **Draft**.
- **Mark ready to send** zaklene polja To, Subject in Body, da se pod vami
  ne morejo spremeniti, in zabeleži, kdo je odobril in kdaj. Še vedno se ne
  pošlje nič.
- **I have sent this** zabeleži, da ste sporočilo poslali. To je zgolj
  evidenca — ne pošlje ničesar, ker sistem tukaj pošte nikoli ne pošilja.
- **Archive this draft** ga označi kot preklicanega in zaklene. Ostane viden,
  tako da priganjanje tega dobavitelja ostane zabeleženo.

## Kaj lahko gre narobe

| Vidite | Pomeni | Kaj storiti | Dobro ali slabo |
|---|---|---|---|
| No drafts listed | Trenutno ni ničesar za poslati, ali pa razvijalec še ni vklopil samodejnih zahtevkov | Če ste tu pričakovali kaj videti, vprašajte razvijalca | Informativno |
| The **To** field is empty | Sistem za tega dobavitelja nima e-poštnega naslova na voljo | Izpolnite ga sami, preden osnutek označite kot ready | Potrebno je vaše dejanje |
| "Not editable once it is…" namesto obrazca | Sporočilo je že označeno kot Ready to send, Sent ali Archived | Ni ničesar za storiti. To je namerno, da se besedilo, ki ga boste ali ste ga že poslali, ne more spremeniti pod vami | Dobro |
| An error after **Save edit** | Vaša sprememba ni bila shranjena. Subject in Body ne smeta biti prazna, potreben je vsaj en prejemnik | Dopolnite, kar manjka, in poskusite znova | Potrebno je vaše dejanje |
| An error after **Mark ready to send**, **I have sent this** or **Archive this draft** | Ni se zgodilo nič. Osnutek je natanko tak, kot je bil | Poskusite znova ali vprašajte razvijalca | Poskusite znova |
| Manufacturer ali Week prikazan kot "—" | To sporočilo nima pripetega zahtevka | Ni ničesar za storiti, besedilo sporočila samo po sebi je še vedno veljavno | Informativno |

## Besede, ki jih uporablja zaslon

| Zaslon pravi | Pomeni |
|---|---|
| **Draft** | Napisal ga je sistem, še ni odobren |
| **Ready to send** | Besedilo ste odobrili. Še ni poslano |
| **Sent** | Sistemu ste sporočili, da ste ga poslali ročno |
| **Archived** | Odločili ste se, da tega ne boste poslali. Ohranjeno, ne izbrisano |
| **Week** | Teden, ki ga to sporočilo pokriva, na primer *Week 34 (17–23 Aug)*. Eno sporočilo na dobavitelja na teden |
| **What it asks:** First ask | Svež zahtevek, nič od navedenega še ni poteklo |
| **What it asks:** Reminder | Bolj nujen ton: bodisi ponovitev, bodisi je nekaj od navedenega že poteklo |
| **Why:** expiry | Dokumentu kmalu poteče veljavnost |
| **Why:** discovery-exhausted | Sistem je iskal povsod in ni našel ničesar, zato sprašuje neposredno dobavitelja |
| **Technical details** | Številka sporočila, njegovo shranjeno stanje in kje stoji ta zahtevek. Nič v njih ne zahteva vaše odločitve |

## Sorodno

- [Expiry](expiry.sl.md) — kaj sproži večino osnutkov
- [Manual](manual.sl.md) — drugi sprožilec, ko iskanje odneha
- [Documents](documents.sl.md) — dokumenti, ki jih eno sporočilo zahteva
- [Vaš dnevni krog](../01-daily-work.sl.md) — kam se Renewal emails umešča v vaš teden
- [Slovar](../glossary.sl.md) — vse besede na enem mestu
