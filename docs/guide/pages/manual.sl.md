# Manual

**V eni povedi:** delo, ki ga sistem sam ni mogel dokončati in čaka na
človeka.

**Stanje:** V uporabi. Preverjeno v kodi 31. 8. 2026.

---

## Ali lahko tu kaj pokvarim?

Ne.

- Nič na tem zaslonu ničesar ne izbriše.
- Edini gumb, ki karkoli spremeni, je **Resolve — retry discovery**. Ta samo
  prosi sistem, naj znova poišče dokumente enega dobavitelja. Ne dotakne se
  nobenega dokumenta, ki ga že imate.
- Iskanje ali brisanje iskanja spremeni le to, kar vidite na zaslonu.
- Če gumb vrne napako, se ni zgodilo nič. Poskusite znova ali vprašajte
  razvijalca.

---

## Kaj je to
Seznam vsega, kar je sistem poskusil narediti sam in ni uspel dokončati. Sem
prihajajo tri različne vrste opravil, in vseh niste dolžni reševati vi sami —
eno vrsto se odloča drugje, ena pa trenutno sploh nima na voljo nobenega
dejanja.

## Kdaj to uporabite
To je seznam za operaterje. V dnevnem krogu pisarna namesto njega uporablja
[Missing documents](missing.sl.md): kaže le iskanja brez zadetka, edino vrsto
kartic, pri kateri ukrepate vi, s proizvajalčevo stranjo za prenos in
spletnim iskanjem na en klik.

## Preden začnete

Nič. Preden odprete ta zaslon, vam ni treba ničesar pripraviti. Poznavanje
imena proizvajalca, imena datoteke, številke certifikata ali številke
dokumenta je koristno le, če iščete eno posamezno opravilo.

## Kaj storite

1. Odprite **Queues & health** v bloku **Operator** na dnu menija, nato
   **Manual**.
2. Da najdete eno opravilo, vtipkajte v **Search** — proizvajalca, ime
   datoteke, številko certifikata, UDI ali številko dokumenta — in pritisnite
   **Filter**. Pritisnite **Clear**, da znova vidite vse.
3. Preberite vsako kartico. Naslov pove, o katerem dokumentu ali izdelku gre;
   besedilo pod njim pove, za kakšno vrsto opravila gre.
4. Le ena vrsta kartice od vas zahteva dejanje tukaj: če ponuja
   **Resolve — retry discovery**, jo pritisnite, da sistem znova poišče, ali
   sledite povezavi **Upload document**, da datoteko dodate sami.
5. Če kartica namesto tega pravi **Open this document in Review**, kliknite
   nanjo. Odločitev se zgodi na zaslonu [Review](review.sl.md), ne tukaj.
6. Če kartica nima ne gumba ne povezave, danes ni ničesar za storiti.

## Kaj se zgodi nato

- Pritisk na **Resolve — retry discovery** prosi sistem, naj znova poišče
  dokumente tega dobavitelja, pod gumbom pa se pojavi vrstica: "The system
  will search again for this item." Če iskanje najde kaj, kar je vredno
  poskusiti, kartica takoj izgine s seznama, še preden je znano, ali je to
  pravi dokument. Če ne najde ničesar, kartica ostane, dokler dokumenta ne
  pridobite sami.
- Odpiranje dokumenta v Review na tem zaslonu ne naredi ničesar. Opravilo
  tukaj se zapre samo, ko dokument tam odobrite ali zavrnete — to ni ločen
  korak.
- Tretja vrsta kartice se sama od sebe nikoli ne spremeni. Ostane, dokler
  razvijalec ne zgradi načina, kako ukrepati na njej.

## Kaj lahko gre narobe

| Vidite | Pomeni | Kaj storiti | Dobro ali slabo |
|---|---|---|---|
| A card with **Resolve — retry discovery** and an **Upload document** link | Sistem je iskal povsod pri tem dobavitelju in ni našel ničesar | Pritisnite **Resolve**, da poskusi znova, ali dokument pridobite sami in ga naložite | Potrebno je vaše dejanje |
| A card that says **Open this document in Review** | Dokument čaka na odločitev | Kliknite nanjo in odločite na zaslonu [Review](review.sl.md) | Potrebno je vaše dejanje, a ne tukaj |
| A card with no button, just a note that there is no action here yet | Opravilo je dokončno spodletelo, nihče pa še ni zgradil načina, kako ga popraviti s tega zaslona | Danes ni ničesar za storiti. Če se zdi nujno, povejte razvijalcu | Zgolj informativno |
| No cards at all | Nič ne čaka na človeka | Ni ničesar za storiti | Dobro |
| *"No open manual task matches…"* after searching | Vaše iskanje se ni ujemalo z nobenim odprtim opravilom | Poskusite z drugo besedo ali pritisnite **Clear** | Poskusite znova |
| An error after pressing **Resolve** | Ni se zgodilo nič. Opravilo je natanko tako, kot je bilo | Poskusite znova ali vprašajte razvijalca | Poskusite znova |

## Besede, ki jih uporablja zaslon

Vsaka kartica prikazuje tudi nekaj sistemovih lastnih oznak. Tu je njihov
pomen.

| Zaslon pravi | Pomeni |
|---|---|
| **discovery-dead-end** | Vrsta "iskal sem povsod, nisem našel ničesar". Rešeno tukaj. |
| **gate-manual** | Vrsta "potrebna je odločitev". Rešeno na [Review](review.sl.md), ne tukaj. |
| **mfr-binding** | Opravilo vrste **gate-manual**, pri katerem sistem tudi ni prepričan, kateremu dobavitelju dokument pripada. |
| **dead-job-followup** | Vrsta "dokončno spodletelo". Zanjo tukaj še ni dejanja. |
| **Payload** | Tehnična podrobnost za kartico, v sistemovem lastnem zapisu. Odprite jo le, če vas razvijalec prosi za to. |
| *"Re-enqueues `discover.group` at interactive priority."* | Preprosto povedano: pritisk na **Resolve** prosi sistem, naj znova poišče dokumente tega dobavitelja, pred rednim delom v ozadju. |

## Sorodno

- [Missing documents](missing.sl.md): seznam iskanj brez zadetka za pisarno
- [Review](review.sl.md) — kjer se opravila, ki potrebujejo odločitev, dejansko odločajo
- [Failed](failed.sl.md) — tretja čakalna vrsta v vašem dnevnem krogu
- [Vaš dnevni krog](../01-daily-work.sl.md): jutro pisarne, ki namesto tega zaslona uporablja Missing documents
- [Slovar](../glossary.sl.md) — vse besede na enem mestu
