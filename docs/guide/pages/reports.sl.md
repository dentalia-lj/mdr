# Weekly reports

**V enem stavku:** kaj je sistem poročal vsak teden, shranjeno tako, da je
poročilo izpred meseca enako berljivo kot tokratno.

**Stanje:** Deluje. Preverjeno glede na kodo, 15. 9. 2026.

**Ta stran je samo za branje.** Z nje se ne pošlje nič.

---

## Kaj je to

Enkrat na teden sistem naredi pregled: kaj je že poteklo, kaj bo kmalu poteklo in
kako gre vrsti obdelave. To poročilo je nekoč obstajalo le znotraj opravila, ki
ga je ustvarilo — zadnjega ste videli na zaslonu [Scheduler](scheduler.sl.md),
starejših pa nikjer.

Zdaj se vsak teden zapiše v datoteko in se ohrani. Ta stran jih našteje, od
najnovejšega naprej, vsakega poimenovanega po tednu, ki ga pokriva ("Week 37
(7–13 Sep)"). [Today](today.sl.md) povezuje zadnja dva tedna z istima imenoma,
kar je pot do tega zaslona.

---

## Kako brati poročilo

Vsako poročilo nosi teden, ki ga pokriva, in čas nastanka, nato eno vrstico z
obema številkama: koliko dokumentov poteče v naslednjih 30 dneh in koliko jih je
že poteklo. Pod tem je ena preprosta vrstica o samem sistemu: koliko njegovih
opravil je spodletelo. Ta vrstica je za razvijalca, ne vprašanje skladnosti.

Nato dve razpredelnici. **Already expired** našteje vsak objavljen dokument,
čigar datum je minil, ne glede na to, kako davno. **Expiring in the next 30
days** našteje tiste, ki prihajajo. 30 dni je isto okno, ki ga uporabljajo
zaslon Expiry, števec v meniju in Today.

Vsaka vrstica navede proizvajalca dokumenta in njegov datum, dokument pa vodi na
svojo stran. Poročilo lahko odprete in natisnete ali shranite kot vsako drugo
stran.

Poročili za tedna W36 in W37, napisani pred uvedbo tega popravka, imata stolpca
Manufacturer in datum prazna. To je bila napaka v tem, kako se je poročilo pisalo, ne manjkajoči
podatki: podatki so obstajali in novejša poročila jih prikazujejo.

---

## Česa ne počne

Nikomur ne pošlje e-pošte. Sistem piše osnutke in poročila; pošlje jih oseba. Če
želite poročilo posredovati, ga odprite in pošljite sami.

---

## Če je seznam prazen

Ali se poročilo še ni izvedlo — prvo se pojavi po tedenskem opravilu — ali pa
temu računalniku ni bilo povedano, kam naj jih shranjuje; v tem primeru stran to
pove in mora nastavitev opraviti razvijalec.

---

## Glejte tudi

- [Scheduler](scheduler.sl.md) — kdaj se tedensko poročilo izvede in ali se je
- [Expiry](expiry.sl.md) — živa različica tega, kar poročilo opisuje
