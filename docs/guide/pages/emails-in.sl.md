# Emails received

**V eni povedi:** kaj je prispelo po e-pošti in kaj je sistem našel priloženo.

**Stanje:** Delno v uporabi. Zaslon sam deluje in natančno prikaže vsako
sporočilo, ki mu je posredovano. Ali mu je karkoli posredovano, je ločeno
vprašanje: samodejno preverjanje nabiralnika je privzeto izklopljeno in danes
ni povezan noben nabiralnik, zato lahko ta seznam ostane prazen, dokler ga
razvijalec ne vklopi. Preverjeno v kodi 15. 9. 2026.

---

Ta zaslon je samo za branje. Ni ničesar, kar bi lahko pritisnili in bi
karkoli spremenilo — vsaka klikljiva stvar na njem je povezava na drug
zaslon.

---

## Kaj je to
Zapis vhodne e-pošte, ki si jo je sistem ogledal: od koga je prišla, o čem se
je zdelo, da govori, in ali je bil priložen uporaben dokument.

## Kdaj to uporabite
Odprite **Emails received** enkrat tedensko, predvsem da potrdite, da je bil
dobaviteljev odgovor zabeležen.

## Preden začnete

Nič. To je seznam samo za branje — odprite ga in preberite.

## Kaj storite

Razen branja ni ničesar za storiti. Če je ob dokumentu prikazan barvni
status, kliknite nanj, da si ga ogledate — pod [Documents](documents.sl.md),
če že šteje, ali na [Review](review.sl.md), če še čaka na odločitev.

## Kaj se zgodi nato

Z branjem se na tem zaslonu nič ne spremeni. Sledenje povezavi do dokumenta
vas odpelje na tisti dokument; na tej strani samo po sebi ni prizadeto
ničesar.

## Kaj lahko gre narobe

| Vidite | Pomeni | Kaj storiti | Dobro ali slabo |
|---|---|---|---|
| No emails at all | Ali ni prispelo nič, ali pa še nihče ni vklopil samodejnega preverjanja nabiralnika | Če ste pričakovali pošto tukaj, vprašajte razvijalca | Informativno |
| A short coloured tag and a one-line note under the subject | Strojno napisana opomba o tem, o čem se je zdelo, da sporočilo govori | Berite jo le kot namig, nikoli kot dokaz. Pred zanašanjem odprite dejansko prilogo ali znova vprašajte dobavitelja | Informativno |
| No note under the subject at all | Sporočilo ni imelo besedila, vrednega povzetka | Ni ničesar za storiti | Informativno |
| *"Reply to request #18, IVOCLAR"* under the subject | Sporočilo je odgovor na to zahtevo za obnovo. Vsak osnutek za zahtevo nosi v zadevi njeno oznako, na primer `[DENT-18]`, in dobaviteljev odgovor jo je ohranil. Klik odpre osnutek te zahteve | Ni ničesar za storiti | Dobro |
| The same line ending *"(matched by sender address)"* | Zadeva ni nosila oznake, zato je sistem naslov pošiljatelja povezal z edinim dobaviteljem z odprto zahtevo. Verjetno ujemanje, ne gotovo | Preden se zanesete nanj, preverite, ali sporočilo res govori o tej zahtevi | Informativno |
| *"summary failed — the body was read but not compressed"* | Opombe tokrat ni bilo mogoče izdelati | Odprite prilogo sporočila neposredno, če jo ima | Informativno |
| *"body below the configured summary floor"* | Sporočilo je bilo prekratko, da bi ga bilo vredno povzeti | Ni ničesar za storiti | Informativno |
| **Published**, **Waiting for review**, **On file** ali **Replaced** ob prilogi | Postala je dokument, beseda pa pove, kje je. Kliknite ime dokumenta, da si ga ogledate | Ni ničesar za storiti | Dobro |
| **skipped**, with a short reason | Priloga ni bila dokument o skladnosti — varnostni list, račun ali datoteka, ki je sistem ni mogel odpreti | Ni ničesar za storiti, razen če menite, da bi morala šteti — potem povejte razvijalcu | Običajno v redu |
| **Being read** | Sistem ima datoteko in jo še bere | Preverite pozneje | Normalno |
| "— already held via…" note under an attachment | Ista datoteka je po drugi poti prispela že prej kot ta e-pošta | Ni ničesar za storiti. E-pošta je potrdila dokument, ki ste ga že imeli, ni ustvarila novega | Informativno |

## Besede, ki jih uporablja zaslon

| Zaslon pravi | Pomeni |
|---|---|
| **sends-documents** | Strojno ugibanje: to sporočilo prinaša dokumentacijo |
| **requests-info** | Strojno ugibanje: to sporočilo od nas nekaj zahteva |
| **acknowledges** | Strojno ugibanje: odgovor, ki potrjuje prejem, zahvala, sporočilo o odsotnosti |
| **other** | Stroj sporočila ni mogel uvrstiti v nič od zgornjega |
| Enovrstična opomba sama | Napisana enkrat, samodejno, ob prejemu sporočila. Izvirno besedilo e-pošte se ne hrani nikoli — le ta kratka opomba |
| "Written for us when the message arrived" | Opomba je naša, ne pošiljateljeva. Kateri program jo je napisal, je pod **Technical details** pod opombo |
| **Already on file** | Ista datoteka je bila že na voljo od drugod. Ni ničesar za storiti |
| **Technical details** | Odprite jih za ime programa, ki je napisal opombo. Nič v njih ne zahteva vaše odločitve |

## Sorodno

- [Documents](documents.sl.md) — kam pristane uporabna priloga
- [Review](review.sl.md) — če dokument še čaka na odločitev
- [Vaš dnevni krog](../01-daily-work.sl.md) — kam se Emails received umešča v vaš teden
- [Slovar](../glossary.sl.md) — vse besede na enem mestu
