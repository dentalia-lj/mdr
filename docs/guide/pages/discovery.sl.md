# Discovery

**V enem stavku:** kaj je sistem že iskal in česa ni iskal še nihče.

**Stanje:** Deluje. Preverjeno glede na kodo, 3. 9. 2026.

**Ta stran je samo za branje.** Ne sproži nobenega iskanja; pokaže le, kaj je
bilo poskušeno in kaj ne.

---

## Kaj je to

Pokritost vam pove, ali imamo dokumentacijo za izdelek. Ne more pa povedati, ali
je zanjo sploh kdo šel iskat. To sta različni dejstvi in eno ob drugem se bereta
zelo različno.

Preverjeno 3. 9. 2026: **za 4.256 od 4.265 medicinskih pripomočkov ni nihče
nikoli iskal dokumentacije**. Skoraj vse, kar imamo, je prišlo iz map
dobaviteljev, ki smo jih prejeli, ne iz iskanja.

---

## Tri stanja

| Stanje | Pomeni |
|---|---|
| **Never searched** | Za dokumentacijo tega izdelka ni šel iskat še nihče |
| **Searched, found nothing** | Iskali smo in se vrnili praznih rok. Resnično drugačna težava |
| **Searched, found something** | Iskanje je vrnilo vsaj en dokument |

Srednje stanje je tisto, ki šteje. Brez njega sta "za ta izdelek nimamo ničesar"
in "ničesar ni mogoče dobiti" videti enako, razlog za pismo dobavitelju pa je
samo eno od njiju.

---

## Kako brati vrstico

Vrstice so združene po družini izdelkov in urejene po tem, koliko medicinskih
pripomočkov je v vsaki, tako da je vrh seznama tam, kjer iskanje prinese največ.

| Stolpec | Pomeni |
|---|---|
| **Manufacturer** | Dobavitelj. Klik vodi na njegovo stran |
| **Group** | Družina izdelkov, ki jo vrstica pokriva |
| **Device items** | Koliko od njih BC označuje kot medicinske pripomočke |
| **Items** | Koliko izdelkov skupaj |
| **Attempts** | Kolikokrat se je iskanje izvedlo |
| **Last searched** | Kdaj, ali **never** |

---

## Kaj storiti glede tega

S te strani se ne začne nič. Odprite dobavitelja in uporabite **Search this
supplier** na njegovi strani, ki prehodi isti seznam v istem vrstnem redu.
Iskanje je namenoma omejeno na posamezen pritisk: gre za resnične zahteve
spletnim mestom dobaviteljev v vljudnem ritmu.

---

## Glejte tudi

- [Manufacturers](manufacturers.sl.md) — kjer se iskanje dejansko sproži
- [Items](items.sl.md) — izdelki sami
- [System status](status.sl.md) — številka pokritosti, ki ji ta stran daje kontekst
