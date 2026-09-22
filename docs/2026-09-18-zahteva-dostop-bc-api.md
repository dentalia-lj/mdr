# Dostop do BC API s strežnika 91.98.42.140

**Osnutek, 18. 9. 2026.** Za Mitja (omrežje, Dentalia) in Luka Vidmarja (BC, b-s.si), v vednost Nataši Palme. Še ni poslano.

**Zadeva:** Dostop do BC API s strežnika 91.98.42.140

---

Pozdravljena Mitja in Luka,

aplikacija za skladnost bo tekla na strežniku **91.98.42.140**. Artikle in
proizvajalce zdaj uvažamo iz izvozov v Excelu (`Artikli`, `Proizvajalci`); to
deluje in s tem bomo začeli. Da bi jih lahko brali neposredno iz Business
Centrala, mora strežnik doseči BC API. Danes ga ne: `denwebnav:7048` je
dosegljiv samo v vašem omrežju in samo prek HTTP.

Prosim za naslednje.

**Mitja (omrežje):**

1. **HTTPS dostop do BC API z javnim imenom** (npr. `bc.dentalia.si`) in
   **dovoljenim samo IP naslovom 91.98.42.140**. Brez HTTPS ne gre, ker bi
   uporabniško ime in geslo potovala po internetu nezaščitena. Če vam je
   ljubši VPN med strežnikom in vašim omrežjem, mi prosim sporočite.

**Luka (BC):**

2. **Servisni uporabnik samo za branje** za strani `allitems` in
   `allmanufacturers`, in kateri način prijave velja (uporabniško ime in
   geslo s ključem za spletne storitve, ali OAuth).
3. **Točen naslov strani `allmanufacturers`** na vaši osnovi
   `.../proddentalia-NAS/api/dentalia/api/v1.0/companies(25ccc3d7-63f8-ec11-9e03-00155d012200)/`.
4. **Katero polje nosi šifro proizvajalca.** 7. 9. ste napisali
   `pteManufCodePrimary`. V vzorcu z 2. 9. je bilo to polje prazno pri vseh
   treh artiklih, šifro (npr. `011`) pa je imelo polje `manufacturerCode`. Je
   bilo `pteManufCodePrimary` medtem napolnjeno? Če izberemo napačno polje, se
   cel katalog uvozi brez proizvajalcev, in to brez napake.
5. **Ali strani podpirata `$select`.** En artikel ima 242 polj, okoli 6 KB, zato
   je celoten katalog danes okoli 112 MB na branje. Mi uporabljamo deset polj;
   s `$select` bi bilo to okoli 5,6 MB, kar tudi vaš strežnik manj obremeni.

**Kako bomo preverili, ko bo dostop odprt** (v BC ne pišemo ničesar):

- najprej po en zapis z obeh strani, da potrdimo polja iz točke 4;
- nato celoten katalog iz BC v testno bazo in primerjava vrstico za vrstico z
  izvozom v Excelu: isto število artiklov, iste šifre artiklov, enaka
  razporeditev po proizvajalcih. Šele ko se ujema, preklopimo uvoz na BC.

Hvala in lep pozdrav,
Denis
