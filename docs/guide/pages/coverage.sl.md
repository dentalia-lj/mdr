# Coverage gaps

**V enem stavku:** katerim artiklom manjka katera dokumentacija — prešteto in
nato našteto, da je mogoče delati po seznamu.

**Stanje:** Deluje. Preverjeno glede na kodo, 11. 9. 2026.

**Ta stran je samo za branje.** Ničesar ne sproži in ničesar ne spremeni.

---

## Kaj je to

[Today](today.sl.md) pove, koliko medicinskih pripomočkov ima izjavo. Ne more pa
povedati, *kateri* izdelki so nepokriti in *kaj* jim manjka. Ta stran odgovori
na oboje.

---

## Tri vprašanja, ne eno

"Ni izjave" pomeni dvoje in oboje je vredno videti:

| Vrzel | Pomeni |
|---|---|
| **No document at all** | Na ta izdelek ni vezan noben objavljen dokument. Najkrajši seznam in najslabši |
| **No Declaration of Conformity** | Ni objavljene izjave, povezane s tem izdelkom prek naše številke artikla, dobaviteljeve številke artikla, UDI ali dobaviteljevega seznama pokritosti. Izdelek ima morda certifikat, navodila za uporabo, izjavo, ki pokriva celoten program proizvajalca, ali izjavo, ki jo je ročno povezala oseba |
| **No MDR or MDD document** | Ni dokumenta, izdanega po uredbi o pripomočkih. Certifikat sistema kakovosti pove nekaj o proizvajalcu, ne o tem izdelku |

Izdelek je lahko na drugem seznamu in ne na tretjem ali obratno. To ni
protislovje — to je razlika med "kakšno vrsto papirja imamo" in "po katerem
predpisu je bil izdan".

Stolpec **What it does hold** je razlog, da so seznami berljivi: "ni izjave" se
bere zelo drugače, kadar ima izdelek certifikat EC, kot kadar nima ničesar.
Pokaže lahko celo *DoC*: to je izjava za celoten program proizvajalca ali
izjava, ki jo je ročno povezala oseba; nobene od njiju prevzemno merilo ne
šteje.

Število **No Declaration of Conformity** je vedno število medicinskih
pripomočkov z zaslona Today, zmanjšano za tiste z izjavo. Oba zaslona štejeta
po istem pravilu, zato se številki ne moreta razlikovati.

---

## Izdelki brez razreda pripomočka

Pod števili boste videli število izdelkov, ki **sploh niso šteti**, ker Business
Central ni povedal, ali so medicinski pripomočki.

Navedeni so in ne skriti. Dokler razred ni izpolnjen, ne moremo povedati, kakšno
dokumentacijo potrebujejo, štetje med vrzeli pa bi si izmislilo delo, ki morda ne
obstaja. Če se to število zdi preveliko, gre za isto težavo kot na zaslonu
[Data quality](data-quality.sl.md).

---

## Kaj storiti z vrstico

Kliknite izdelek, da vidite vse, kar je nanj vezano, ali proizvajalca, da vidite,
kaj imamo od tega dobavitelja, in da sprožite iskanje ali prošnjo.

---

## Glejte tudi

- [Items](items.sl.md) — celoten katalog namesto vrzeli
- [Discovery](discovery.sl.md) — ali je za te sploh kdo kdaj iskal
- [Today](today.sl.md): stavek o izjavah, čigar manjkajoči artikli so
  seznam **No Declaration of Conformity**
