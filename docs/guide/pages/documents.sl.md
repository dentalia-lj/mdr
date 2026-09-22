# Documents

**V enem stavku:** vsak dokument, ki ga je sistem našel ali prejel, na enem
mestu, ne glede na to, v kateri fazi je.

**Stanje:** Deluje. Preverjeno glede na kodo, 15. 9. 2026.

---

## Ali lahko tu kaj pokvarim?

Skoraj nič — z eno izjemo, in ta je povrnljiva.

- Nič tukaj ne izbriše dokumenta, ga ne odobri in ne objavi. Odobritev se
  zgodi na [Review](review.sl.md).
- **Izjema: Reopen this document.** Pojavi se le pri dokumentu, ki ga je
  nekdo že zavrnil, in ga vrne na [Review](review.sl.md) med čakajoče. Ga
  **ne** odobri in ne vrne izdelkov, na katere je bil vezan — ti ostanejo
  zavrnjeni, dokler ni vsak posebej ponovno odprt. Če ga po pomoti ponovno
  odprete, ga na Review preprosto znova zavrnite. Za ime vas vpraša zato, ker
  se razveljavitev tuje odločitve zabeleži.
- **Look up in EUDAMED** le vpraša evropsko bazo podatkov, kaj ve o napravi
  tega dokumenta. Na dokumentu samem to nič ne spremeni.
- Če gumb vrne napako, se ni zgodilo nič. Poskusite znova ali vprašajte
  razvijalca.

---

## Kaj je to

Vsak dokument, ki ga sistem hrani — tak, ki šteje in je objavljen, tak, ki
čaka na odločitev, zavrnjen, nadomeščen z novejšim, ali dejansko od
dobavitelja, a ne pokriva ničesar, kar imate na zalogi. S tega seznama se nič
nikoli ne odstrani, tudi dokument, ki ga je nekdo zavrnil, ne.

Odprite en dokument in vidite: kaj trdi, da je, katere izdelke pokriva, od kod
je prišla datoteka, vsako dejstvo, ki ga je sistem prebral z njega, in stran,
s katere ga je prebral — ter, če je poteklo ali kmalu poteče, ali že kdo
poganja dobavitelja za novo različico.

---

## Kdaj to uporabite

- Želite preveriti en konkreten dokument — njegove datume, kaj pokriva ali od
  kod je prišel.
- Revizor vpraša, od kod je prišlo neko dejstvo. Vsaka vrednost tukaj nosi
  točno stran, s katere je bila prebrana.
- Brskate po proizvajalcu, vrsti dokumenta ali statusu, ne po izdelku.

---

## Preden začnete

Nič. Številko dokumenta, ime proizvajalca, številko certifikata ali del imena
datoteke potrebujete le, če želite po njem iskati — pustite iskalno polje
prazno, če želite brskati po vsem.

---

## Kaj storite

1. Kliknite **Documents** v meniju na levi.
2. Zožite seznam s **Type** ali **Status**, ali v **Search** vnesite ime
   proizvajalca, ime datoteke, številko certifikata, UDI ali številko
   dokumenta, nato pritisnite **Filter**. Pritisnite **Clear**, če želite
   začeti znova.
3. Kliknite številko dokumenta, da odprete njegovo stran.
4. Preberite identifikacijsko tabelo: kaj dokument je, pod katero regulativo
   spada, njegove datume in od kod je bil pridobljen.
5. Kliknite **archived file**, da odprete dejanski PDF, ali **stored text**,
   da vidite točno to, kar je sistem prebral iz njega.
6. Poglejte **Covered items** za to, katere izdelke pokriva, in odprite
   **Technical details** pod *Where every value came from* za vsako dejstvo, ki
   ga je sistem izluščil, s točno stranjo in besedilom, od koder izvira.
7. Če dokument še čaka na odločitev, kliknite **review this document**, da
   greste neposredno nanjo na [Review](review.sl.md).
8. Če dokument navaja Basic UDI-DI in želite hiter navzkrižni pregled,
   pritisnite **Look up in EUDAMED**.

---

## Kaj se zgodi nato
- **Look up in EUDAMED** teče v ozadju. Sporočilo potrdi, da je bilo vloženo v
  vrsto, ali da je bilo že danes preverjeno. Rezultat se pojavi kot nova
  tabela na isti strani, ko je gotovo — osvežite, da ga vidite.
- Vse drugo na tej strani je le za branje. Nič, kar tu kliknete, ne spremeni
  statusa dokumenta.

---

## Kaj lahko gre narobe

| You see | Pomeni | Kaj storiti | Dobro ali slabo |
|---|---|---|---|
| *EUDAMED lookup queued.* | Sistem bo kmalu preveril napravo tega dokumenta v EUDAMED | Nič — preverite pozneje | Dobro |
| *Already looked up today.* | Ta dokument je bil danes že preverjen | Nič — počakajte, da se konča | Dobro |
| *This document carries no Basic UDI-DI — nothing to look up.* | Dokument ne navaja kode, po kateri bi lahko iskali v EUDAMED | Ni kaj storiti — to je pogosto in ni napaka | Nevtralno |
| A document's manufacturer shows as plain text, not a link | Imena, natisnjenega na dokumentu, ni bilo mogoče ujemati z natanko enim od vaših dobaviteljev | Tu ni kaj storiti — to je zgolj informativno | Nevtralno |
| A document shows *— (none extracted)* for manufacturer | Sistem s strani sploh ni mogel prebrati imena proizvajalca | Odprite PDF in preverite, ali je čitljiv | Nevtralno, razen če bi moral biti resničen dokument |

---

## Besede, ki jih uporablja zaslon

| The screen says | Pomeni |
|---|---|
| The small coloured label next to a document (*Published*, *Waiting for review*, *On file*, *Replaced*, *Rejected*) | Glejte [status dokumenta](../glossary.sl.md) |
| **Type**, prikazano kot *Declaration of Conformity*, *EC certificate*, *Instructions for use*, *ISO certificate* | Za kakšno vrsto dokumenta gre. Kratke oznake, ki jih sistem shranjuje, niso prikazane |
| **Coverage scope**, shown as *group* | Ta dokument pokriva eno poimenovano skupino izdelkov |
| **Coverage scope**, shown as *manufacturer* | Ta dokument pokriva celoten program tega dobavitelja |
| **Cited certificate** | Certifikat priglašenega organa, na katerega se ta izjava sklicuje, če obstaja |
| A date marked *(review)* or *(inherited)* | Ni resničen datum poteka, natisnjen na dokumentu — glejte [petletno pravilo](../glossary.sl.md) |
| **Where every value came from** | Dokazi za vsako dejstvo, zloženi pod **Technical details** skupaj s prstnim odtisom datoteke in podlago, na kateri je bila narejena vsaka povezava do izdelka. Odprite, kadar vpraša presojevalec; pri vsakdanjem delu spreglejte |
| **Reopen this document** | Vrne zavrnjen dokument na Review. Edini način, da se zavrnitev razveljavi |

---

## Sorodno

- [Items](items.sl.md) — isti dokumenti, razvrščeni po izdelku
- [Review](review.sl.md) — kjer se dokument, ki čaka, odobri ali zavrne
- [Manufacturers](manufacturers.sl.md) — celotna zgodovina dokumentov enega dobavitelja
- [Expiry](expiry.sl.md) — kateri dokumenti potekajo ali so že potekli
- [Glossary](../glossary.sl.md) — vse besede na enem mestu
