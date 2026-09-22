# Ingest

**V enem stavku:** to je drug, bolj neposreden način uvoza seznama
izdelkov — zapiše takoj, brez predogleda.

## Stanje

Delno v uporabi. Preverjeno s kodo 31. 8. 2026. Nalaganje datoteke, ki je
že na strežniku, deluje. Možnost žive povezave z Business Centralom ne —
še ni zgrajena in vedno varno spodleti.

## Ali lahko tu kaj pokvarim?

Da, malo — preberite to, preden to uporabite.

- Za razliko od **Import** vam ta zaslon ne pokaže predogleda. Pritisk na
  gumb takoj zapiše v vaš seznam izdelkov.
- Tudi tukaj se nič nikoli ne izbriše. Izdelki, ki jih v datoteki ni,
  ostanejo pri miru.
- A tu ni varovalke, ki bi ujela napačno datoteko, preden ta zapiše. Če
  ga usmerite na staro ali napačno datoteko, se vaš seznam izdelkov takoj
  posodobi tako, da se ujema z njo. In tak ostane, dokler kdo ne uvozi
  prave datoteke.
- Če nekaj spodleti na sredi poti, sistem ne spremeni čisto ničesar.
  Bodisi se v celoti zaključi, bodisi ne zapiše ničesar.
- Izbira možnosti povezave z Business Centralom trenutno vedno spodleti
  — varno, brez zapisa. Preprosto še ne deluje.
- Večina bralcev naj namesto tega uporabi **Import**. Naredi isto stvar
  in vam pokaže, kaj se bo spremenilo, preden se spremeni.

## Kaj je to

Drug način nalaganja seznama izdelkov iz Business Centrala, poleg
**Import**. Razlika: ta zapiše v vaše zapise v trenutku, ko pritisnete
gumb, brez predhodnega predogleda.

## Kdaj to uporabite

Za večino bralcev redko, če sploh kdaj. Uporabite ga le, ko vam
razvijalec ali IT natančno pove, na katero datoteko naj ga usmerite. Za
preglednico na vašem lastnem računalniku raje uporabite **Import** —
enak učinek, a najprej pokaže predogled.

## Preden začnete

- Ta zaslon vam ne dovoli izbrati datoteke z vašega lastnega
  računalnika. Prebere le datoteko, ki že leži na Dentalijinem
  strežniku, ali — ko bo zgrajena — živo povezavo z Business Centralom.
- Potrebujete točno lokacijo datoteke od IT-ja ali razvijalca. Vnos
  napačne povzroči, da uvoz spodleti, a ne zapiše nič.
- Ne izbirajte **bc_odata** kot vir. Še ni zgrajen in bo vedno spodletel.
- Pod naslovom zaslon izpiše odstavek tehničnega besedila, napisanega za
  razvijalce, ne za vas. Del je zastarel. Ne upoštevajte ga.

## Kaj storite

1. V naslovno vrstico brskalnika neposredno vpišite `/ingest`. **Namenoma ni v
   meniju na levi** — posezite raje po [Import](import.sl.md), ki vzame datoteko
   z vašega računalnika in vam pred zapisom pokaže razliko. To stran uporabite
   le, kadar vam tako naroči razvijalec.
2. Pod **Source** izberite **csv**. **bc_odata** pustite pri miru; še ne
   deluje.
3. Izberite datoteko s seznama pod "Pick a file under…" ali vnesite
   natančno pot, ki vam jo je dal razvijalec, pod "Or type a path
   manually".
4. Pustite **Priority** na **interactive**, razen če vam je naročeno
   drugače.
5. Kliknite gumb z oznako **Enqueue ingest.run** — njegovo lastno
   interno ime za "začni ta uvoz".

## Kaj se zgodi nato

Stran se ne osveži znova. Pojavi se vrstica: *Import queued. The item list is
read and the new items start looking for their documents.*

- Tu ni predogleda. Delo poteka v ozadju.
- Ko se konča, se pojavi na seznamu **Recent runs** na dnu te strani —
  kot blok surovih tehničnih podrobnosti za gumbom **show**, ne kot
  prijazen povzetek.
- Če želite vedeti, kaj se je dejansko spremenilo, je najvarnejše mesto
  za preverjanje pozneje **Items**.
- Seznam **Recent runs** ne prikazuje samo vašega uvoza. Prikazuje
  vsako nedavno opravilo celotnega sistema, zato se vas večina vrstic ne
  bo tikala.

## Kaj lahko gre narobe

| Vidite | Pomeni | Kaj storiti | Dobro ali slabo |
|---|---|---|---|
| *a CSV/Excel path is required (pick one or type a path)* | Niste izbrali ali vnesli lokacije datoteke | Izberite eno s seznama ali vnesite točno pot, ki vam jo je dal IT | Slabo — nič ni bilo zapisano |
| *company is required for a bc_odata ingest* | Izbrali ste povezavo z Business Centralom, a pustili **Company** prazno | Namesto tega uporabite **csv** z datoteko — možnost povezave še ne deluje | Slabo — nič ni bilo zapisano |
| *delta_since '' is not a valid datetime* | **Delta since** je bilo puščeno prazno ali ni bilo izpolnjeno prek izbirnika datuma | Namesto tega uporabite **csv** z datoteko | Slabo — nič ni bilo zapisano |
| *Import queued…* in nato nič drugega | Normalno — delo poteka v ozadju | Čez trenutek preverite **Recent runs** spodaj | V redu |
| Vrstica na **Recent runs** za vaš poskus prikaže *failed* in omeni, da povezava ni zgrajena | Izbrali ste možnost povezave z Business Centralom | Namesto tega uporabite **csv** z datoteko ali se obrnite na razvijalca | Slabo, a varno — nič ni bilo zapisano |
| Blok surovega besedila pod izidom opravila | Sistemovo lastno tehnično poročilo za to opravilo, ne posebej za vaše | Običajno ga ne upoštevajte. Razvijalec ga lahko prebere, če je kaj videti narobe | Nevtralno |

## Besede, ki jih uporablja zaslon

| Zaslon pravi | Pomeni |
|---|---|
| csv / bc_odata (Source) | Od kod prihaja seznam izdelkov: datoteka (**csv**) ali živa povezava z Business Centralom (**bc_odata** — še ni zgrajena) |
| **interactive** / **delta** / **sweep** (Priority) | Kako hitro pride to na vrsto v primerjavi z drugim čakajočim delom. **interactive** je na vrsti prva |
| Enqueue ingest.run | Interno ime gumba. Pomeni "začni ta uvoz" |
| dedupe key | Številka potrdila za točno to zahtevo |

## Sorodno

- [Import](import.sl.md) — varnejša pot noter, s predogledom, preden je karkoli zapisano
- [Items](items.sl.md) — vaš seznam izdelkov, za preverjanje, kaj se je dejansko spremenilo
- [Glossary](../glossary.sl.md) — vse besede na enem mestu
