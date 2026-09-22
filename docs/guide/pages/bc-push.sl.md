# Business Central

**V eni povedi:** kaj bi Business Centralu povedali o posameznem artiklu —
prikazano vam, preden mu to povemo.

**Stanje:** V uporabi. Preverjeno v kodi 11. 9. 2026.

---

## Ali lahko tukaj kaj pokvarim?

Ne.

- Ogled nikoli ničesar ne pošlje. Odprtje strani vam samo pokaže seznam.
- Tudi **Pošlji** se ne poveže z Business Centralom neposredno. Najprej pokaže,
  kaj namerava uvrstiti v vrsto, in vas prosi za potrditev; šele po potrditvi
  delo preda sistemu, ki ga opravi v ozadju.
- Vrstica na vrhu strani jasno pove, ali je pošiljanje vklopljeno. Kadar je
  izklopljeno — kar je običajna nastavitev — stran še vedno izračuna, kaj bi
  se spremenilo; le do Business Centrala ne pride.
- Nič tukaj ne spremeni dokumenta, povezave ali česar koli v tem registru.
  Spremenijo se lahko samo tri polja na kartici artikla v Business Centralu.
- Dvakratno pošiljanje iste stvari je neškodljivo: sistem primerja, kaj Business
  Central že ima, in pošlje samo tisto, kar se razlikuje. Če se ni nič
  spremenilo, pritisk na Pošlji ne pošlje ničesar.
- Če se izkaže, da je vrednost napačna, popravite dokument tukaj in znova
  pritisnite Pošlji. Naslednje pošiljanje jo prepiše.

---

## Kaj to je

Tri polja na kartici vsakega artikla v Business Centralu:

- **Veljavna izjava o skladnosti** — ali imamo izjavo, ki pokriva ta artikel?
- **Veljaven CE certifikat** — ali imamo certifikat priglašenega organa, ki
  pokriva ta artikel?
- **Povezava na skladišče** — povezava iz Business Centrala nazaj na stran tega
  artikla pri nas, da lahko vsak, ki gleda kartico, vidi dejanske dokumente.

## Kaj pomeni "pokriva ta artikel"

Dokument mora pokrivati *artikel sam ali skupino artiklov, ki ji pripada*.
Dokument, ki pokriva samo proizvajalca, ne šteje.

Prav ta razlika je bistvo. Certifikat, ki pravi "sistem vodenja kakovosti tega
podjetja je odobren", je izjava o dobavitelju. Ni dokazilo o konkretnem
artiklu, po katerem sprašuje kupec, in če bi ga v Business Central zapisali,
kot da je, bi sodelavcu povedali nekaj, česar ne moremo podpreti.

Pomemben je tudi datum. Dokument, ki mu je potekla lastna navedena veljavnost
ali veljavnost certifikata, na katerega se opira, ne šteje. Dokument brez
navedenega datuma poteka je v redu — večina izjav ga nima.

## Artikli, ki jih tukaj ne boste videli

Artikli, ki jih sistem še nikoli ni obdelal, niso na seznamu in se nikoli ne
pošljejo.

Kljukica ima samo dve legi in nobena ne pomeni "še nismo pogledali". Če bi za
artikel, ki ga ni nihče pregledal, poslali "ne", bi sodelavec ob branju kartice
upravičeno sklepal, da smo preverili in ničesar našli. Da takih artiklov ne
spreminjamo, je poštenejši odgovor.

## Kdaj jo uporabite

Redko, in ni nujno. Dnevno opravilo v ozadju samo od sebe prehodi katalog, od
najstarejšega naprej, tako da Business Central ostaja usklajen brez pritiskanja.

To stran uporabite, kadar želite, da določena sprememba pride zdaj — na primer
po potrditvi svežnja dokumentov, na katere sodelavec čaka.

## Preden začnete

Nič.

## Kaj naredite

1. Kliknite **Business Central push** v bloku **Operator** na dnu menija.
2. Preberite vrh strani: ali je pošiljanje sploh kje vklopljeno, in — če je
   artiklov, ki bi se lahko spremenili, več kot 1000 — koliko jih je v resnici,
   saj spodnji seznam vedno prikaže le prvih 1000.
3. Preberite naslov pod tem: koliko artiklov bi se spremenilo in koliko se jih
   že ujema.
4. Preglejte seznam. Vsaka vrstica prikaže en artikel, eno polje in vrednost, ki
   bi bila poslana. "First send" pomeni, da Business Centralu o tem artiklu še
   nikoli nismo ničesar povedali.
5. Če je videti pravilno, pritisnite **Pošlji**. Vpraša vas, koliko artiklov
   nameravate uvrstiti v vrsto, in vas opomni, ali je pošiljanje dejansko
   vklopljeno. Pritisnite **Yes, send them**, da nadaljujete, ali **Cancel**,
   da se umaknete brez uvrščanja česarkoli v vrsto.
6. Stran potrdi, koliko artiklov je bilo uvrščenih v vrsto.

Če želite poslati en sam artikel, odprite raje stran tega artikla in tam
pritisnite **Update Business Central**.

## Kaj se zgodi potem

- Delo je uvrščeno v vrsto, ne opravljeno. Zgodi se v ozadju, običajno v minuti
  ali dveh.
- Vrnite se na to stran: artikli, ki so bili uspešno poslani, se preselijo med
  "already match" in izginejo s seznama.
- Artikel, ki po nekaj minutah ostane na seznamu, ni bil poslan. To je vredno
  povedati razvijalcu — najpogosteje pomeni, da Business Central za ta artikel
  sploh nima kartice.
- Naenkrat je na seznamu največ 1000 artiklov. Kadar jih je več, to pove
  opomba na vrhu strani, ki navede tudi resnično skupno število. Nič ni
  izpuščeno; ostali pridejo na vrsto z dnevnim opravilom.
