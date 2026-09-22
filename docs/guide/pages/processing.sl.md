# Processing

**V enem stavku:** ta stran odgovori na eno vprašanje — koliko dela sistem
trenutno opravlja.

**Stanje:** V živo. Preverjeno na delujoči strani 31. 8. 2026.

---

## Kaj je to

Ta stran je samo za branje. Karkoli naredite tukaj, ne spremeni ničesar.

Šteje dokumente, ki jih sistem trenutno aktivno bere ali preverja, in to
število razčleni po tem, v kateri fazi je vsak od njih.

---

## Kdaj to uporabite

- Sem vas je pripeljal podatek **Documents being read** na zaslonu
  [System status](status.sl.md) in želite podrobnosti za njim.
- Želite vedeti, ali sistem trenutno kaj počne ali miruje.

---

## Preden začnete

Nič. Dovolj je, da ste prijavljeni.

---

## Kaj storite

1. Odprite **Queues & health** v bloku Operator na dnu menija, nato
   **Processing**.
2. Preberite skupno število in razpredelnico razčlenitve pod njim.

Tu ni ničesar za odločiti. Če je skupno število visoko, počakajte — običajno
se samo umiri. Če ostane visoko dolgo časa in se nikoli ne premakne, povejte
razvijalcu, namesto da bi poskušali sami popraviti.

---

## Kaj se zgodi nato

Vidite eno število — **documents in flight** — in razpredelnico z eno vrstico
za vsako fazo, v kateri je lahko dokument:

| Stolpec | Pomeni |
|---|---|
| **Stage** | V kateri fazi branja in preverjanja je dokument |
| **Documents** | Koliko različnih PDF-jev je v tej fazi |
| **Jobs** | Koliko delovnih enot to predstavlja |

**Documents** in **Jobs** nista isto število. En PDF ima lahko hkrati več kot
eno delovno enoto — dokument, ki se preverja, ima lahko že začeto tudi
odločitev o vpisu — zato je stolpec Jobs pogosto višji, vrstice pa se ne
seštejejo v skupno število na vrhu.

V praksi boste tu videli le peščico faz — dokument se **bere** (iz njega se
izluščita besedilo in datumi), se **preverja** (preverjajo se njegovi datumi
in sklici na izdelke), ali pa se sprejema odločitev o njegovem **vpisu**.
Zgodnejši koraki, kot je iskanje dokumentove spletne strani, se tu nikoli ne
prikažejo, ker ta stran šteje samo dokumente, ki so že najdeni in se zdaj
obdelujejo.

---

## Kaj lahko gre narobe

| Kar vidite | Kar to pomeni | Kaj storiti | Dobro ali slabo |
|---|---|---|---|
| Skupno število nič | Trenutno se ne obdeluje nič | Normalno, zlasti izven delovnega časa | V redu |
| Stalno, premikajoče se skupno število | Dokumenti se berejo in preverjajo, kot je pričakovano | Nič | V redu |
| Visoko skupno število, ki se ob več preverjanjih ne spremeni | Morda je nekaj obtičalo | Povejte razvijalcu | Razvijalčev problem |

---

## Besede, ki jih uporablja zaslon
| Kar piše na strani | Kar to pomeni |
|---|---|
| **documents in flight** | Skupno število, o katerem govori ta stran. Povsod drugod se isto imenuje **Being read** |
| Raw stage names like `extract.doc`, `validate.doc` | Interno ime koraka. `extract.doc` je branje dokumenta; `validate.doc` je njegovo preverjanje |
| **Jobs** | Delovne enote. Glejte [Besede o lastnem delu sistema](../glossary.sl.md) v slovarju |

---

## Sorodno
- [System status](status.sl.md): njena ploščica **Documents being read** je isto
  število kot skupno število na tej strani
- [Review](review.sl.md) — kamor gre dokument, ko ga sistem konča preverjati
  in ni prepričan vanj
- [Glossary](../glossary.sl.md) — vse besede na enem mestu
