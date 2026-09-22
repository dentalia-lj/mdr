# Items

**V enem stavku:** tukaj poiščete en izdelek in vidite, katero dokumentacijo o
skladnosti ima.

**Stanje:** Deluje. Preverjeno glede na kodo, 15. 9. 2026.

---

## Ali lahko tu kaj pokvarim?

Večinoma ne.

- Nič na tej strani ne izbriše dokumenta, in samo gledanje ničesar ne spremeni.
- **Re-discover documents** sistem le prosi, naj znova poišče dokumentacijo. Sam
  po sebi nič ne doda, odstrani ali odobri. Brskalnik pred tem prosi za
  potrditev.
- **Covers this item** in **Does not** odločata, ali en dokument, ki ga že
  vidite, velja za ta izdelek. Ne dotikata se dokumenta samega ali katerega
  koli drugega izdelka, ki ga morda pokriva.
- **Re-open** znova postavi dokument, ki ste ga zavrnili, pred vas za ponovni
  pregled. V nobenem primeru se nič ne izgubi.
- **Update Business Central** pošlje tri skladnostna polja tega izdelka nazaj v
  Business Central, če se razlikujejo od tega, kar ta že ima. Brskalnik pred
  tem prosi za potrditev. Tu ne spremeni ničesar, dokler pa je pisanje nazaj
  izklopljeno -- kar je običajna nastavitev -- izračuna, kaj bi poslal, in ne
  pošlje ničesar.
- Vsaka odločitev se shrani z vašim imenom in časom.
- Če gumb vrne napako, se ni zgodilo nič. Poskusite znova ali vprašajte
  razvijalca.

---

## Kaj je to

Seznam vseh izdelkov v vašem katalogu, ob vsakem pa koliko dokumentacije o
skladnosti je zanj shranjene: koliko dokumentov šteje, koliko jih še čaka na
odločitev in koliko jih je bilo nadomeščenih z novejšim.

Odprite en izdelek in vidite vse: njegovega proizvajalca, dobaviteljevo lastno
številko artikla, kaj mora kot distributer imeti, in vsak dokument, povezan z
njim — vključno z dokumenti, ki jih stranka ali Business Central še ne vidita,
ker jih še nihče ni potrdil.

Dve različni stvari sta lahko na eni vrstici "v čakanju": dokument sam in
povezava tega dokumenta s tem konkretnim izdelkom. Dokument lahko že šteje,
medtem ko njegova povezava s tem konkretnim izdelkom še čaka na osebo. Za to
sta ob vsaki vrstici znački **Document** in **Link** — lahko si nasprotujeta.

---

## Kdaj to uporabite

- Morate preveriti, katera dokumentacija pokriva en izdelek — na primer pred
  odgovorom stranki ali revizorju.
- Želite pregled, kateri izdelki še imajo vrzeli.
- Vas je sem pripeljalo iskanje z [Documents](documents.sl.md) ali
  [Manufacturers](manufacturers.sl.md).

---

## Preden začnete

Nič. Vsak, ki se lahko prijavi, lahko išče in odpre stran izdelka. Številko
artikla, ime izdelka ali ime proizvajalca potrebujete le, če želite iskati po
njih — pustite iskalno polje prazno, če želite videti vse izdelke.

---

## Kaj storite

1. Kliknite **Items** v meniju na levi.
2. Pod naslovom je vrstica **Show: All items · Without a declaration · Never
   searched**. Druga odpre [Coverage gaps](coverage.sl.md), artikle brez izjave,
   tretja pa [Discovery](discovery.sl.md), družine izdelkov, ki jih še nihče ni
   iskal. Obe sta bili do 14. septembra 2026 svoji postavki v meniju.
3. Vnesite številko artikla, ime izdelka ali ime proizvajalca v iskalno polje
   in pritisnite **Filter**. Pritisnite **Clear**, če želite začeti znova.
4. Kliknite številko artikla, da odprete njegovo stran.
5. Preberite tabelo skladnosti pri vrhu: kaj mora ta izdelek kot distributer
   imeti in kaj je zanj dejansko shranjeno. Za pomen posamezne vrstice glejte
   [kaj morate dejansko imeti](../glossary.sl.md).
6. Pod njo preberite **Linked documents** za same dokumente, ne glede na to, v
   kateri fazi je posamezen.
7. Pod tem preberite **Have we searched for this?**. Pove vam, ali je kdo za ta
   izdelek sploh kdaj iskal dokumentacijo — to je drugo vprašanje kot to, ali
   jo imamo. Prazen seznam dokumentov pri *Never searched* pomeni, da nihče še
   ni iskal, ne pa da dokumentov ni.
8. Kliknite vrstico dokumenta, da odprete njegovo stran, **file**, da vidite
   dejanski PDF, ali **text**, da vidite točno to, kar je sistem prebral iz
   njega.
9. Če ob vrstici vidite **Covers this item** in **Does not** in ste
   prepričani, kaj je pravilno, pritisnite enega od njiju. Ta se pojavita
   šele, ko dokument sam že šteje — če dokument še čaka na odločitev, to
   najprej uredite na [Review](review.sl.md).
10. Če se kaj zdi narobe, zastarelo ali manjkajoče, pritisnite
   **Re-discover documents**, da sistem znova poišče dokumentacijo.
11. Če Business Central pri tem izdelku kaže napačen skladnostni odgovor,
   pritisnite **Update Business Central**. Kaj tri polja pomenijo in kdaj je
   vsako od njih resnično, piše na strani [Business Central](bc-push.sl.md).

---

## Kaj se zgodi nato
- **Re-discover documents** v ozadju sproži novo iskanje. Kratko sporočilo to
  potrdi, drugje na strani se takoj nič ne spremeni — preverite pozneje.
- **Update Business Central** deluje enako: posodobitev uvrsti v vrsto in to
  potrdi. Potrditev pove, kaj od dvojega se dogaja: *the worker sends it* ali
  *sending is switched off, so nothing will reach Business Central*. Drugi
  pritisk istega dne ne naredi ničesar. Če izdelek še nikoli ni šel skozi
  sistem, se ne pošlje nič -- odgovor, ki ga nimamo, ne sme priti v Business
  Central kot "ne".
- **Covers this item**, **Does not** in **Re-open** se shranijo takoj, z vašim
  imenom in časom. Značke na strani se posodobijo kmalu zatem; če spremembe ne
  vidite takoj, osvežite stran.
- Nič tu ne odstrani dokumenta. Pritisk na **Does not** pove le, da ta
  konkreten dokument ne pokriva tega konkretnega izdelka — dokument sam in
  vsak drug izdelek, ki ga morda pokriva, ostaneta nedotaknjena.

---

## Kaj lahko gre narobe

| You see | Pomeni | Kaj storiti | Dobro ali slabo |
|---|---|---|---|
| *Re-discovery queued at interactive priority.* | Sistem bo kmalu iskal nove dokumente za ta izdelek | Nič — preverite pozneje | Dobro |
| *Already queued today.* | Vi ali nekdo drug ste to danes že zahtevali | Nič — počakajte, da se konča | Dobro |
| *This item has no group yet — nothing was queued.* | Sistem še ni ugotovil, kateri skupini izdelkov ta pripada, zato ne more iskati | Povejte razvijalcu | Slabo |
| A short technical confirmation after pressing **Covers this item** / **Does not** / **Re-open**, with a reference number | Vaša odločitev je shranjena in se zapisuje | Nič — to je normalno, čeprav je besedilo tehnično | Dobro |
| *No documents linked to this item yet.* | Za ta izdelek še nihče ni našel dokumentacije | Preden karkoli sklepate, preberite **Have we searched for this?** spodaj | Odvisno — slabo, če izdelek potrebuje izjavo o skladnosti in je nima |
| **Never searched** | Za ta izdelek še nihče ni nikoli iskal dokumentacije. Večina dokumentov, ki jih imamo, je prišla kot paketna dostava dobavitelja, ne iz iskanja | Če je izdelek pomemben, pritisnite **Re-discover documents** | Samo po sebi ni slabo — a prazen seznam dokumentov tu ne dokazuje ničesar |
| **Searched, found nothing** | Iskali smo in se vrnili praznih rok | Vprašajte dobavitelja neposredno. Samodejno iskanje je naredilo, kar je lahko | Slabo, če izdelek potrebuje izjavo o skladnosti |
| **Searched, nothing found, sent to Missing documents** | Iskali smo povsod, kjer znamo, nismo našli ničesar in odprli nalogo, da zadevo prevzame človek | Odprite [Missing documents](missing.sl.md) — je že na tem seznamu | Slabo, če izdelek potrebuje izjavo o skladnosti, a naslednji korak je že zabeležen |
| **Searched, found something** | Iskanje je našlo vsaj en dokument | Nič — dokumenti so v seznamu zgoraj | Dobro |

---

## Besede, ki jih uporablja zaslon

| The screen says | Pomeni |
|---|---|
| **Item no.** | Dentalijina lastna številka izdelka, tista, ki je natisnjena na artiklu |
| **Medical device** | Ali je Business Central ta izdelek označil kot medicinski pripomoček. *not stated* pomeni, da je polje v Business Centralu prazno |
| **Published / Waiting for review / Replaced** | Koliko dokumentov tega izdelka šteje, koliko jih še čaka na odločitev in koliko jih je bilo nadomeščenih z novejšim |
| **Covers this item** | Ali ta dokument šteje prav za ta izdelek. Dokument je lahko objavljen, povezava prav do tega izdelka pa še vedno čaka na odločitev |
| The small coloured label next to a document (e.g. *Published*, *Waiting for review*) | Glejte [status dokumenta](../glossary.sl.md) |
| **Manufacturer's article no.** | Dobaviteljeva lastna številka artikla za ta izdelek. Glejte [REF oz. številka artikla](../glossary.sl.md) |
| **Where we looked** | Kje je sistem iskal — *The manufacturer's known pages* (proizvajalčeve znane strani), *An address we had before* (naslov, ki smo ga že imeli), *The web* (splet), *EUDAMED* |
| **Found something / Nothing found / Not tried** | Ali je to mesto kaj vrnilo, ni vrnilo ničesar ali ni bilo preizkušeno |
| **Technical details** | Shranjene vrednosti za zaslonom: oznaka kataloga, koda proizvajalca v Business Centralu in podlaga, na kateri je bil vsak dokument povezan s tem izdelkom. Glejte [kako se dokument ujema z izdelkom](../glossary.sl.md). Pri vsakdanjem delu jih lahko prezrete |

---

## Sorodno

- [Documents](documents.sl.md) — vsak dokument posebej, ne razvrščen po izdelku
- [Manufacturers](manufacturers.sl.md) — celoten katalog enega dobavitelja naenkrat
- [Expiry](expiry.sl.md) — kaj poteka v celotnem naboru izdelkov
- [Review](review.sl.md) — kjer se dokument sam odobri ali zavrne
- [Glossary](../glossary.sl.md) — vse besede na enem mestu
