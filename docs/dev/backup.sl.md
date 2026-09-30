# Varnostno kopiranje in obnova

**Stanje:** v veljavi, napisano 2026-09-28. Ta dokument **pokriva vse o
varnostnem kopiranju**: kaj je zaščiteno, ravni kopij, kaj teče samodejno in
kdaj, kaj naredi človek, kako obnoviti, kaj storiti, ko gre kaj narobe, in
česa ta rešitev **ne** ščiti. Naročeno s ponudbo 2026092301.

**Dva jezika, ena vsebina.** [backup.md](backup.md) je angleška različica,
ta je slovenska za Dentalio (odločeno 2026-09-30). Spremembe gredo v obe v
istem commitu; `tests/test_backup_script.py` pade, če se ukazi ali poglavja
razlikujejo.

Zasnova je standardna: preverjen `pg_dump` na strežniku in
[restic](https://restic.readthedocs.io/) iz paketa distribucije, ki kopira na
Hetzner Storage Box prek SFTP. En skript,
[`scripts/backup.sh`](../../scripts/backup.sh), ki ga zaganja cron. Vsaka
obnova so navadni ukazi restic in Postgres, izpisani spodaj.

---

## 1. Kaj je zaščiteno

| Podatki | Kje so | Jih je mogoče ponovno ustvariti? |
|---|---|---|
| Baza: register, dokazila, revizijska sled, odločitve pregledovalcev, recepti, zgodovina prenosov | Postgres (`PGDATA_HOST`) | **Ne.** Odločitve pregledovalcev so izgubljene za vedno; razčlenjevanje le s ponovnim plačilom |
| Arhiv: vsak prenesen dokument | `ARCHIVE_HOST` | **Ne.** Proizvajalci dokumente umikajo; e-poštne priloge pridejo enkrat |
| Uvozi: korpus in izvozi iz BC | `IMPORTS_HOST` | Delno, s ponovno prošnjo |
| Nastavitve: `.env`, `caddy/users/` | repozitorij na strežniku | Samo ročno |
| Koda | GitHub | Da. Vsaka kopija zapiše, **kateri commit** je tekel (`code.txt`) |

**Cilji** (iz ponudbe): največ **1 ura** izgubljenih podatkov, največ **4 ure**
od odločitve za obnovo do delujočega sistema na novem strežniku.

---

## 2. Ravni

| Raven | Kje | Kaj | Kdaj | Ščiti pred |
|---|---|---|---|---|
| **L0** | ta strežnik, `backups/hourly/` | izvoz baze (preverjeno obnovljiv), vloge, `code.txt`; hrani se zadnjih 48. Poleg tega izvoz, ki ga `deploy.sh` naredi pred vsako migracijo (zadnjih 5) | vsako uro | napačnim uvozom, ponesrečenim popravkom, slabo migracijo |
| **L1** | Hetzner Storage Box, restic (`BACKUP_REPO`) | zadnji izvoz L0, vloge in `code.txt`, arhiv, uvozi, `.env`, `caddy/users/` | vsako uro, takoj po L0 | izgubo diska ali strežnika |
| **L2** (neobvezno) | zunanji ponudnik, restic (`BACKUP_REPO_2`) | isto kot L1, kot samostojen repozitorij | vsako uro | izgubo ali zaklep računa pri Hetznerju |

**Dokler L1 ne teče, varnostne kopije v smislu ponudbe ni.** L0 je na istem
disku, ki ga ščiti. Arhiv ne potrebuje kopije L0: cevovod datotek v njem nikoli
ne spreminja ne briše, zato je na strežniku sam svoja kopija, L1 pa njegova
varnostna kopija.

### Kaj strežnik lahko uniči in česa ne

Ponudba obljublja, da »strežnik kopij ne more izbrisati ali prepisati«.
Natančno:

| | Lahko strežnik, ali kdor vdre vanj, to uniči? |
|---|---|
| L0 | **Da.** Je na strežnikovem disku |
| L1, repozitorij | **Da.** Strežnikova prijava SFTP tja piše in tam čisti |
| L1, posnetki Storage Boxa | **Ne.** Hetznerjeve lastne dnevne kopije celega boxa: samo za branje pod `/.zfs/snapshot`, upravljajo se le v Hetzner Console. BX11 jih hrani 10: **10 dni** nazaj |
| L2 | Le če ponudnik to dovoli. Z object lock / verzioniranjem **ne** |

Obljuba torej drži prek posnetkov Storage Boxa (in L2 z object lock), za 10
dni. To se Dentalii pove ob predaji.

---

## 3. Kaj teče samodejno

Crontab uporabnika `denis` na strežniku (brez roota). Časi so v UTC.

```cron
7 * * * *   cd /srv/compliance/app && ./scripts/backup.sh hourly >> backups/cron.log 2>&1
37 3 * * 0  cd /srv/compliance/app && ./scripts/backup.sh weekly >> backups/cron.log 2>&1
```

| Kdaj | Kaj | Če ne uspe |
|---|---|---|
| vsako uro ob :07 | **L0:** izvoz baze, poskusna obnova v začasno bazo, shranitev skupaj z vlogami in `code.txt`; izvozi nad 48 se brišejo. **L1/L2:** `restic backup` zadnjega izvoza, arhiva, uvozov in nastavitev na vsako nastavljeno raven. Po uspešni kopiji L1 sledi ping na heartbeat | opozorilo »hourly failed« (L0) ali »L1 failed«, L0 ostane. Ponavlja se vsako uro do popravka |
| ob nedeljah ob 03:37 | `restic forget --prune` (48 urnih, 14 dnevnih, 8 tedenskih, 12 mesečnih, 10 letnih) in `restic check --read-data-subset=5%` na vsaki ravni | opozorilo »prune failed« / »failed its check« |
| ob vsaki namestitvi | `deploy.sh` izvozi bazo pred migracijo | namestitev se ustavi |

Raven, ki v `.env` ni nastavljena, se preskoči, zato je crontab pred Storage
Boxom in po njem enak.

- **Ena ključavnica.** `deploy.sh` in `backup.sh` si delita `backups/.lock`:
  namestitev počaka na kopijo (do 15 min), kopija na namestitev (do 30 min).
- **Zasebne datoteke.** Izvozi, vloge in `code.txt` nastanejo z načinom 0600:
  so celoten register, strežnik pa si deli z Dentalijinim uporabnikom
  PocketBase.
- **Ena vrstica na zagon** v `backups/backup.log`:
  ```
  2026-09-28T12:30:49Z mode=hourly level=L0 result=ok dump=7.0M secs=7
  2026-09-28T12:30:50Z mode=hourly level=L1 result=ok secs=1
  ```
  `result=PARTIAL` pomeni, da je restic posnetek shranil, nekaterih datotek pa
  ni mogel prebrati; opozorilo to sporoči, posnetek pa šteje.

### Opozorila in kaj strežnik ne more sporočiti

Napake gredo na `ALERTS_WEBHOOK_URL` (kanal ntfy cevovoda), z visoko
prioriteto, z naslovom `Dentalia backup: ...`.

**Mrtev strežnik, ustavljen cron ali pokvarjen kanal opozoril tega sami ne
morejo sporočiti.** Za to je `BACKUP_HEARTBEAT_URL`: zunanji nadzornik
(healthchecks.io, Better Stack, Uptime Kuma ali podobno), ki ga strežnik
pokliče po vsaki uspešni kopiji L1 in ki sproži alarm, ko klici izostanejo
2 uri. **Dokler ga ni, obljuba »več kot 2 uri brez kopije sproži opozorilo«
drži le, dokler strežnik, cron in ntfy delujejo.** Odloženo, § 8.

Stanje na hitro: `./scripts/backup.sh status`.

---

## 4. Kaj naredi človek

| Kaj | Kdo | Kdaj |
|---|---|---|
| Hrani geslo za restic in ključ SSH za Storage Box | dve osebi v Dentalii in Denis | vedno (§ 7) |
| Bere opozorila o kopijah in alarme nadzornika | kdor jih prejema | ko pride (§ 6) |
| Naredi kopijo pred tveganim posegom | kdor ga izvaja | pred popravljalnim orodjem z `--apply`, velikim backfillom, ročnim SQL: `./scripts/backup.sh hourly` |
| Test obnove | Denis | ob predaji; četrtletno, če je naročeno (§ 5.3) |
| Vklopi posnetke Storage Boxa | Mitja (samo on ima dostop do Hetzner Console) | enkrat, samodejno dnevno |

### Upravljanje: vklop, izklop, stanje, zagon zdaj

Vse na strežniku, kot `denis`, v `/srv/compliance/app`.

| Za | Naredi | Učinek |
|---|---|---|
| **Pogled stanja** | `./scripts/backup.sh status` | zadnji zagoni iz dnevnika, najnovejši izvozi L0, zadnji posnetki po ravneh |
| **Pogled urnika** | `crontab -l` | dve vrstici iz § 3 ali nič |
| **Branje zgodovine** | `tail -20 backups/backup.log`; napake v `backups/cron.log` | ena vrstica na zagon in raven |
| **Kopija takoj** | `./scripts/backup.sh hourly` | natanko to, kar zažene cron; počaka, če namestitev drži ključavnico |
| **Tedenski opravek takoj** | `./scripts/backup.sh weekly` | čiščenje in preverjanje vseh nastavljenih ravni |
| **Ročna uporaba restica** (`check`, `snapshots`, `find`, `restore`) | najprej `export RESTIC_REPOSITORY=sftp:storagebox:dentalia RESTIC_PASSWORD_FILE=/srv/compliance/secrets/restic-password`; brez tega se restic ustavi z »Please specify repository location« | navaden restic nad L1 |
| **Vklop vsega** | `crontab -e`, dodaj vrstici iz § 3 | vsako uro od naslednjega :07 |
| **Premor** | `crontab -e`, pred obe vrstici `#` | nič ne teče; obstoječi izvozi in posnetki ostanejo. **Nobeno opozorilo ne pove, da so se kopije ustavile** (le nadzornik bi, § 3) |
| **Nadaljevanje** | odstrani `#` | naslednji :07 teče normalno; ničesar ni treba nadoknaditi |
| **Izklop L1 (ali L2), L0 ostane** | v `.env` zakomentiraj `BACKUP_REPO` (ali `BACKUP_REPO_2`) | naslednji zagon naredi le izvoz L0; repozitorij in njegovi posnetki ostanejo nedotaknjeni |
| **Ponovni vklop L1** | odkomentiraj vrstico | naslednji zagon nadaljuje v istem repozitoriju in naloži le spremembe |
| **Trajna odstranitev** | izbriši vrstici v cronu; nato lahko izbrišeš `backups/hourly/` | repozitorij na Storage Boxu se s tem **ne** izbriše; to je ločen, namerni korak v Hetzner Console |

Za ustavitev zadošča cron. V ozadju ne teče nič drugega: ne storitev, ne
kontejner, ne demon.

---

## 5. Obnova

Za obnovo so potrebni le restic, Docker in repozitorij s kodo. **Repozitorij
prebere vsak restic od različice 0.14 naprej** (resticov format 2, »readable
using restic 0.14.0 or newer«), zato zadošča paket distribucije na katerem
koli računalniku. Vaja 2026-09-28 z Debianovim resticom 0.14.0 (§ 9).

Pred vsakim spodnjim ukazom restic:

```bash
export RESTIC_REPOSITORY='sftp:storagebox:dentalia'     # L1; see § 7 for the SSH setup
export RESTIC_PASSWORD_FILE=/srv/compliance/secrets/restic-password
```

### 5.1 Napačni podatki, strežnik deluje: iz L0

Napačen uvoz, ponesrečen popravek, slaba migracija.

```bash
docker compose stop worker web
ls -1t backups/hourly/*.dump | head              # pick the last one from before it happened
cat backups/hourly/<stamp>.code.txt              # the commit it ran on
docker compose exec -T postgres sh -c 'psql -q -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE \"$POSTGRES_DB\" WITH (FORCE)" -c "CREATE DATABASE \"$POSTGRES_DB\""'
docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --exit-on-error' < backups/hourly/<stamp>.dump
git checkout <commit>                            # only if a deploy caused it; else deploy.sh re-applies the bad migration
./scripts/deploy.sh                              # starts worker and web, verifies
```

Izvozi ob namestitvah (`backups/<stamp>-ran-<commit>.dump`) delujejo enako:
runbook § Rolling back.

**Kaj pomeni vrnitev nazaj**, poleg izgube dela po izvozu:

- Opravila, končana po izvozu, tečejo **znova**: razčlenjevanje se plača
  dvakrat, `extraction_cost` pa prvega plačila ne pozna več. Paketi, oddani po
  izvozu, se oddajo znova.
- Vrednosti, poslane v BC po izvozu, **ostanejo v BC**, `bc_push_log` pa zanje
  ne ve; preverjanje odstopanj razlike ne opazi. Če je pisanje v BC vklopljeno,
  jih najprej popiši.
- Osnutki e-pošte, označeni kot poslani, so spet neposlani: ne pošlji jih
  dvakrat.
- Neškodljivo: prevzem pošte IMAP samo bere.

### 5.2 Ena datoteka iz arhiva

```bash
restic find --host dentalia '<file name>'        # which snapshots hold it
umask 077; restic restore <snapshot> --target /srv/compliance/restore --include '<file name>'
sudo cp -an /srv/compliance/restore/srv/compliance/archive/. /srv/compliance/archive/
rm -rf /srv/compliance/restore
```

`cp -an` doda le manjkajoče. Poškodovano datoteko je treba prej umakniti
(`sudo mv`: arhiv je v lasti roota).

### 5.3 Nov strežnik: iz L1

Resnični primer: strežnika ni več, nov dobi restic, kakršnega ima njegova
distribucija, in obnovi s Storage Boxa.

**Najprej:** če stari strežnik morda še teče, ga ustavi (napol živ worker še
naprej bere IMAP, pošilja v BC in porablja denar). Če je bil vdor, v Hetzner
Console prekliči njegov ključ SSH za Storage Box in obnovi iz posnetka boxa
(§ 5.4).

Potrebno: geslo za restic in ključ SSH za Storage Box (§ 7); prostor na disku
za dvakratni arhiv. **Box odgovarja samo znotraj Hetznerjevega omrežja**
(»External reachability« je izklopljen): nov strežnik pri Hetznerju ga doseže
neposredno; katerikoli drug računalnik gre prek takega, ki ga doseže, s
`ProxyJump` v vnosu `storagebox` v `~/.ssh/config` (§ 9, 2026-09-30).

1. Docker, uporabnik v skupini `docker`, repozitorij v `/srv/compliance/app`,
   mape: [deployment.md § 1](deployment.md). Nato `sudo apt install restic`.
2. Geslo in ključ SSH v `/srv/compliance/secrets/` (mapa 0700, datoteke 0600),
   vnos `storagebox` v `~/.ssh/config` (§ 7) in oba `export` od zgoraj.
3. Prenesi vse:
   ```bash
   umask 077
   restic snapshots --latest 3
   restic restore latest --target /srv/compliance/restore
   R=/srv/compliance/restore/srv/compliance      # restic keeps the original paths
   cat $R/app/backups/offsite/code.txt           # the commit the server ran
   ```
4. `git checkout <commit>` v repozitoriju.
5. Nastavitve: `cp $R/app/.env .env && chmod 600 .env` in
   `cp -a $R/app/caddy/users/. caddy/users/`. Preveri, da poti v `.env`
   (`PGDATA_HOST`, `ARCHIVE_HOST`, `IMPORTS_HOST`, `BACKUP_*`) tu obstajajo.
6. Datoteke: `sudo cp -an $R/archive/. /srv/compliance/archive/` in
   `cp -an $R/imports/. <IMPORTS_HOST>/`.
7. Baza, v nov, prazen Postgres. Čakaj prek **TCP**: slika najprej zažene
   začasen strežnik samo na vtičnici in preverjanje prek vtičnice uspe, ko se ta
   ravno ustavlja (vaja 2026-09-28 je padla točno tako):
   ```bash
   docker compose up -d postgres
   until docker compose exec -T postgres sh -c 'pg_isready -q -h 127.0.0.1 -U "$POSTGRES_USER"'; do sleep 1; done
   D=$R/app/backups/offsite
   # roles first (the dump's GRANTs name them), except the superuser this stack connects as
   docker compose exec -T postgres sh -c 'grep -vE "ROLE \"?$POSTGRES_USER\"?( |;)" | psql -q -U "$POSTGRES_USER" -d postgres' < $D/roles.sql
   docker compose exec -T postgres sh -c 'psql -q -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\" WITH (FORCE)" -c "CREATE DATABASE \"$POSTGRES_DB\""'
   docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --exit-on-error' < $D/dentalia.dump
   ```
8. `./scripts/deploy.sh` (zgradi, po potrebi migrira, zažene worker in web,
   preveri), nato `docker compose restart caddy` in dostop: spletno mesto v
   gostiteljevem Caddyju in DNS, če se je naslov spremenil
   ([deployment.md § 7](deployment.md)).
9. Spet kopije: enkrat ročno `./scripts/backup.sh hourly`, nato crontab (§ 3).
   L1 nadaljuje v istem repozitoriju.
10. `rm -rf /srv/compliance/restore`: v njem sta baza in `.env` nešifrirana.

### 5.4 Iz posnetka Storage Boxa (po vdoru)

Posnetki so mape na boxu, samo za branje. Restic usmeri na kopijo repozitorija
v posnetku in beri brez ključavnice:

```bash
export RESTIC_REPOSITORY='sftp:storagebox:.zfs/snapshot/<snapshot-name>/dentalia'   # /home/.zfs/snapshot on port 23
restic snapshots --no-lock
restic restore latest --no-lock --target /srv/compliance/restore
```

Izberi posnetek boxa izpred vdora, nato nadaljuj pri § 5.3, korak 4. Natančna
pot pod `.zfs` se potrdi na pravem boxu.

---

## 6. Kaj če

| Zgodi se | Naredi |
|---|---|
| **Opozorilo »hourly failed«** (L0) | `tail backups/backup.log backups/cron.log`. Prejšnji izvozi so še v `backups/hourly/` |
| **»L1 failed«** | L0 je v redu. Preveri box v Hetzner Console in `ssh storagebox`; naslednja ura sama nadoknadi |
| **»L1 skipped unreadable files«** | ročno zaženi `./scripts/backup.sh hourly`, da vidiš katere; navadno datoteka, ki jo je worker zapisal z načinom, ki ga `denis` ne more brati |
| **»failed its check«** | **ne čisti.** `restic check --read-data` za celotno sliko, nato resticovi ukazi `repair` (dokumentacija restic, »Troubleshooting«) |
| **Nadzornik sproži alarm** | strežnik, cron, docker ali omrežje ne deluje. Prijavi se; če se ne da, je strežnik izgubljen (§ 5.3) |
| Izvoz se ne da obnoviti | zagon se ustavi, namesto da bi hranil slab izvoz; `.partial` v `backups/hourly/` ostane za pregled. Videno 2026-09-18: osiroteli tipi (runbook § Rolling back) |
| Disk je poln | izvozi L0 imajo okoli 7 MB (×48); poskusna obnova potrebuje prostor za drugo bazo. `df -h`, nato sprosti prostor |
| Namestitev med kopiranjem | druga počaka na ključavnico |
| Nobeno opozorilo ne pride | kanal ali cron ne deluje: `./scripts/backup.sh status`, `crontab -l`, `curl -fsS -d test "$(sed -n 's/^ALERTS_WEBHOOK_URL=//p' .env)"` |
| Vdor v strežnik | § 2: L0 in repozitorij L1 sta lahko uničena. Prekliči ključ za box, § 5.4 |
| Izguba računa pri Hetznerju | L0 in L1 izgineta skupaj. L2, če obstaja |
| Geslo je izgubljeno | nobene kopije ne more odpreti nihče. Poti nazaj ni |

---

## 7. Nastavitev in ključi

### L0 (zdaj)

```bash
chmod 600 .env backups/*.dump        # .env holds every secret; older deploy dumps predate 0600
./scripts/backup.sh hourly            # once by hand
crontab -e                            # the two lines in § 3
```

Enkrat preveri opozorilo, ne da bi se dotaknil česar koli živega: za en zagon
usmeri L1 na repozitorij, ki ne obstaja,
`BACKUP_REPO=/nonexistent BACKUP_PASSWORD_FILE=/dev/null ./scripts/backup.sh hourly`,
in počakaj, da pride »L1 failed«. (L0 v istem zagonu teče normalno.)

Kdo še lahko bere te datoteke: `/srv/compliance` je skupine `deploy`
(deployment.md § 1.1), in kdor je v `deploy` ali `docker`, lahko bere izvoze
ne glede na njihov način. `getent group deploy docker` pokaže, kdo.

### L1 (ko je Mitja naročil box)

1. `sudo apt install restic` (Ubuntu 26.04 ima 0.18.1).
2. Na boxu: vklopi SSH. (Podračun, omejen na eno mapo, je urejenejša možnost;
   živa nastavitev uporablja glavni račun `u679983`, ki deluje enako in prav
   tako ne more do posnetkov boxa.)
3. Ključi, na strežniku:
   ```bash
   mkdir -p /srv/compliance/secrets && chmod 700 /srv/compliance/secrets
   head -c 32 /dev/urandom | base64 > /srv/compliance/secrets/restic-password
   ssh-keygen -t ed25519 -N '' -f /srv/compliance/secrets/storagebox-key
   chmod 600 /srv/compliance/secrets/*
   ```
   Javni ključ namesti na box (Hetzner Console, ali
   `cat storagebox-key.pub | ssh -p 23 <user>@<user>.your-storagebox.de install-ssh-key`
   s ključem, ki ga box že sprejme).
4. `~/.ssh/config`:
   ```
   Host storagebox
       HostName u679983.your-storagebox.de
       User u679983
       Port 23
       IdentityFile /srv/compliance/secrets/storagebox-key
       IdentitiesOnly yes
   ```
   Nato enkrat `ssh storagebox ls` in primerjaj ključ gostitelja s tistim v
   Hetzner Console (ED25519 `SHA256:XqONwb1S0zuj5A1CDxpOSuD2hnAArV1A3wKY7Z3sdgM`
   dne 2026-09-30).
5. `.env`:
   ```
   BACKUP_REPO=sftp:storagebox:dentalia
   BACKUP_PASSWORD_FILE=/srv/compliance/secrets/restic-password
   ```
6. `./scripts/backup.sh init`, nato `./scripts/backup.sh hourly` (prvi zagon
   prebere vse), nato `./scripts/backup.sh status`.
7. Mitja vklopi samodejne dnevne posnetke (Hetzner Console; nihče drug nima
   dostopa). Nato preveri, da prijava na box pod `/.zfs` ne more ničesar izbrisati.

Pot repozitorija je **relativna**: `sftp:storagebox:dentalia` je na boxu
`/home/dentalia`, domača mapa prijave. Pot pod `.zfs` (§ 5.4) se potrdi, ko
posnetki obstajajo.

### L2 (samo, če jo Dentalia želi)

Katerikoli ponudnik, ki ga restic podpira. `BACKUP_REPO_2` je niz repozitorija
za restic; ključi v slogu S3 gredo v datoteko, ki jo imenuje
`BACKUP_REPO_2_ENV_FILE` (`AWS_ACCESS_KEY_ID=...`, `AWS_SECRET_ACCESS_KEY=...`),
način 0600. Nato `./scripts/backup.sh init`. Prednost ima object lock /
verzioniranje: šele to naredi L2 odporno na zlorabljen strežnik.

### Ključi

- **Geslo za restic** odpre L1 in L2. Brez njega nobene kopije ne more odpreti
  nihče, tudi mi ne. Je na strežniku (urna kopija ga potrebuje), pri Denisu in v
  Dentalii v upravitelju gesel **in** v zapečateni papirnati kopiji, ki jo hranita
  dve imenovani osebi. »Ključ hrani naročnik« pomeni, da ima Dentalia svojo
  kopijo.
- **Ključ SSH za Storage Box** (in prijava na box) je potreben za obnovo iz L1.
  Hrani se enako. Nobeden od ključev ni v varnostni kopiji.
- **Če geslo uide, ga ni dovolj zamenjati.** Resticovo geslo le ovija glavni
  ključ; `restic key add` / `key remove` ničesar ne šifrira znova, zato staro
  geslo in katerakoli kopija repozitorija (na primer posnetek boxa) še vedno
  odpreta vse. Rešitev je nov repozitorij z novim geslom in izbris starega,
  ko ima novi dovolj zgodovine.

---

## 8. Odločitve in omejitve

Odločil Denis 2026-09-30 (`docs/decisions.md`):

- **Zaščita pred brisanjem: 10 dni je sprejeto.** Posnetki Storage Boxa so
  edina kopija, do katere strežnik ne more, in segajo 10 dni nazaj. Brez L2.
  Dentalia to izve ob predaji, skupaj s tem, da izgube računa pri Hetznerju ne
  preživi nič.
- **Zunanji nadzornik (heartbeat): kasneje.** Dokler ga ni, mrtev strežnik,
  ustavljen cron ali pokvarjen kanal opozoril opazijo ljudje, ne varnostna
  kopija (§ 3).
- **Ta dokument obstaja tudi v angleščini** in oba se vodita usklajeno.

**Ni kopirano:** druge aplikacije na strežniku (PocketBase), nastavitve
gostiteljevega Caddyja, crontab (je v § 3), ključi (hranijo jih ljudje).

---

## 9. Vaje

### 2026-09-28, razvojno okolje, restic 0.14.0 (Debianov paket)

Razvojna baza 64 MB (izvoz 7,0 MB); arhiv 1.425 datotek, prekopiran v mapo v
lasti roota, kot je strežnikov `ARCHIVE_HOST`; uvozi; skupaj 1,48 GiB. L1 je
bil lokalni repozitorij namesto Storage Boxa.

| Korak | Čas | Rezultat |
|---|---|---|
| Samo L0 (L1 ni nastavljen) | 12 s | izvoz, vloge, `code.txt`, vse 0600 |
| L1 `init`, prva kopija | 50 s | 930 MiB shranjenih |
| Naslednja urna | 8 s | 2,5 MiB dodanih |
| Tedensko: forget, prune, check 5 % | | brez napak |
| Neberljiva datoteka | | posnetek shranjen, `result=PARTIAL`, opozorilo poslano |
| Heartbeat | | ping po vsaki uspešni kopiji L1 |
| `restic restore latest` | 26 s | celotno drevo pod ciljem, izvirne poti |
| Baza v **nov, prazen** Postgres (§ 5.3, korak 7) | 11 s | `document` 896, `evidence` 7.588, `audit_log` 2.175, `item_document` 9.134, vloge 2: **enako** kot živa baza |
| Arhiv s `cp -an` v prazno mapo | 14 s | 1.425 datotek, **enak** SHA-256; drugi zagon ni spremenil ničesar |

Vaja je ujela in popravila (§ 5.3): prva obnova baze je padla, ker je
`pg_isready` prek vtičnice uspel, ko se je začasni zagonski strežnik slike
ravno ustavljal; čakanje prek TCP je to rešilo.

### 2026-09-30, strežnik, L1 na pravem Storage Boxu (restic 0.18.1)

`init` na `sftp:storagebox:dentalia`, nato prvi ročni `hourly`: 4.351
datotek, 2,54 GiB prebranih, 1,34 GiB shranjenih, skupaj **13 s** (L0 3 s,
L1 9 s). `restic check --read-data-subset=5%`: brez napak. Izvoz, obnovljen z
boxa, je imel enak SHA-256 kot tisti na strežniku. Cron L1 prevzame od
naslednjega :07 brez spremembe crontaba.

### 2026-09-30, test obnove v začasnem kontejnerju (test iz ponudbe)

Na razvojnem računalniku (WSL), v čistem kontejnerju `ubuntu:26.04`, ki je
imel samo geslo za restic in ključ za Storage Box: `apt install restic`
(0.18.1), nato `restic restore latest` s pravega boxa. Box je neposredno prijavo
od zunaj Hetznerja zavrnil, zato je SSH šel prek strežnika (`ProxyJump`);
strežnik je podatke le posredoval. Primerjava: kopija na strežniku ob 11:45:57
UTC in števci produkcije, prebrani v isti sekundi.

| Korak | Čas | Rezultat |
|---|---|---|
| Namestitev restica, SSH | 16 s | |
| `restic restore latest` | 4 min 10 s | 4.815 datotek, 2,54 GiB |
| Postgres 16 pripravljen, vloge, `pg_restore` (§ 5.3, korak 7) | 9 s | brez napak |
| Baza | | `document` 1.088, `evidence` 8.099, `item_document` 11.840, `audit_log` 508, `fetch_log` 1.611, `item_mirror` 15.968, `job` 20.140, `schema_migrations` 70, vloge 2: **enako** kot produkcija |
| Izvoz | | **enak** SHA-256 kot na strežniku |
| Arhiv | | 1.572 datotek, **enak** SHA-256, vsaka |
| Nastavitve | | `.env` in `caddy/users/` sta prisotna |

Približno 5 minut od praznega kontejnerja do obnovljene baze in arhiva, prek
domače povezave. Ni pokrito: zagon aplikacije na obnovljenih podatkih
(obnovljeni `.env` ima produkcijske poverilnice, zato bi worker bral pravi
nabiralnik in porabljal denar) in čas za pripravo novega strežnika.

**Še ni izvedeno:** posnetki boxa in `--no-lock` iz `.zfs` (§ 5.4).

Prejšnja različica te rešitve (restic v Dockerju, lokalni repozitorij,
kopiran naprej, lasten skript za obnovo) je bila isti dan trikrat pregledana in
zamenjana s to standardno; ugotovitve pregledov, ki še veljajo, so vgrajene
zgoraj (preverjeni izvozi, zasebne datoteke, ena ključavnica, opozorila, ki
morajo priti, heartbeat, koraki obnove, pošten pregled brisanja).
