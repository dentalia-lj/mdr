# Server audit: 91.98.42.140, 2026-09-18

**Status:** dated reading, not a live document. A read-only survey over SSH as
`denis`, in two passes on 2026-09-18: before Docker was installed (~08:40 UTC)
and after Denis installed it (~09:05 UTC). Nothing was written on the server by
the survey. For a newer reading, re-run § 8 and write a new dated file.

Read with [dev/deployment.md](dev/deployment.md), which is the generic install
guide. § 6 below lists where this particular server departs from what that
guide assumes.

---

## 1. Verdict

The host is big enough and Docker is now installed and running. Nothing blocks
a first `docker compose up`, but four things should be settled before it:

1. **Ingress.** The machine is a public VPS, not a server on Dentalia's LAN, and
   a host Caddy already owns ports 80/443 for Dentalia's live app (§ 4).
2. **Memory.** No swap, and our `worker` has no memory limit, on a box shared
   with a production app (§ 6.3).
3. **Log rotation.** None configured anywhere (§ 6.4).
4. **`docker-buildx`** is not installed (§ 6.5).

The BC OData route cannot work from this host (§ 5.2). Masters come by file,
which is the v1 route anyway.

## 2. Host

| | Reading |
|---|---|
| Provider | Reverse DNS `static.140.42.98.91.clients.your-server.de` (Hetzner) |
| Hostname | `incisivus` |
| OS | Ubuntu 26.04.1 LTS (`resolute`), x86_64 |
| CPU / RAM | 4 cores, 7.6 GiB, **no swap**. 6.8 GiB available after the Docker install |
| Disk | One 150 GB ext4 root (`/dev/sda1`), 141 GB free |
| Security module | AppArmor present, no SELinux, so bind mounts need no `:z` |
| Clock | UTC, NTP synchronised (chrony) |
| Uptime | 11 days at the time of the reading; `unattended-upgrades` is running |

**Accounts.** Three home directories exist, two of them in the `sudo` group.
Sudo requires a password (`sudo -n` refuses).
Denis, 2026-09-18: not a blocker. Steps that need root are run by hand in an
interactive session; an unattended SSH command cannot answer the prompt.

## 3. Docker (after the install)

| | Reading |
|---|---|
| Source | Ubuntu archive, not Docker's apt repo (no `download.docker.com` source) |
| Packages | `docker.io` 29.1.3, `docker-compose-v2` 2.40.3, `containerd` 2.2.2, `runc` 1.4.0 |
| `docker-buildx` | **Not installed.** Candidate 0.30.1 in the Ubuntu archive |
| Service | `docker` active and enabled, `containerd` active |
| Access | `denis` is in the `docker` group; `docker ps` works without sudo |
| Daemon | root `/var/lib/docker`, storage `overlayfs`, cgroup `systemd`/v2 |
| Logging | driver `json-file`, **no `/etc/docker/daemon.json`**, so no rotation |
| State | 0 images, 0 containers, default networks only |

For comparison, the dev machine runs engine 20.10.24 and Compose v5.3.0. The
compose file has never been run under Compose 2.40.

## 4. What else lives here

This is the "other app on the same server" in PHASES.md § 2.1, B2.

- **Host Caddy** (systemd, `/etc/caddy/Caddyfile`, Let's Encrypt with
  `it@dentalia.si`) owns public ports 80 and 443. Its admin API is on
  `127.0.0.1:2019`.
- It serves `team.dentalia.si`, `demo.team.dentalia.si` and
  `beta.team.dentalia.si`, reverse-proxying `/api/*` and `/_/*` to two
  **PocketBase** instances (`pocketbase@prod`, `pocketbase@demo`) on
  `127.0.0.1:8090` and `:8091`. They run as user `dentalia` from
  `/srv/dentalia/{prod,demo}`. Together they use about 140 MB RSS.
  `banana.dentalia.si`, `demo.banana.dentalia.si`, two `pricepilot.si` names
  and the server's reverse-DNS name all 302 to the `team` hosts, so those names
  are taken. Host Caddy is v2.11.4; ours is the `caddy:2.8` image.
- The Caddyfile has no `import` of a sites directory: every site is inline in
  the one file.
- `/srv/compliance/` exists (created 2026-09-18 09:00), empty, owned
  `root:root` 755, so `denis` cannot write into it without sudo.
- Listening after the install: `22`, `80`, `443` public; `2019`, `8090`, `8091`
  and `53` on loopback; plus a new loopback listener on `35283` that appeared
  with the Docker install (process not identifiable without sudo). **Port 8000
  is free.**
- The whole Caddyfile was read in a second pass the same morning.

## 5. Network

### 5.1 Outbound

HEAD requests from the server, no proxy in the environment:

| Target | Answer | Needed for |
|---|---|---|
| `api.anthropic.com` | 404 (reachable) | Extraction, at runtime |
| `pypi.org/simple/` | 200 | Image build |
| `mcr.microsoft.com/v2/` | 200 | Image build (Playwright base) |
| `registry-1.docker.io/v2/` | 401 (reachable) | Image build (`postgres`, `caddy`, `python`) |
| `github.com` | 200 | Code delivery, if cloned on the server |
| `api.search.brave.com` | 301 (reachable) | Discovery, once unheld |

### 5.2 Business Central

`denwebnav` does not resolve, so nothing can reach `denwebnav:7048`. BC is on
Dentalia's LAN and this is a public VPS. Consequences:

- **BC OData ingest (deployment.md § 6.4 Route C) is impossible from here**
  without a VPN or a public BC endpoint. Items and manufacturers come by file:
  `/import` upload, or a file dropped under `IMPORTS_HOST`.
- The BC item-card hyperlink (`/item/*?k=`) goes the other way, from a BC user's
  browser to this server, so it works as long as those users can reach the
  public hostname.

### 5.3 Firewall

`ufw` is installed; its status needs sudo and was not read. Docker-published
ports bypass ufw rules, but our compose publishes on loopback only, so nothing
of ours is exposed by that.

## 6. Where this server departs from deployment.md

### 6.1 Ingress (§ 7 of the guide)

The guide assumes a LAN server and offers three routes. Here it is option 2,
with the host Caddy as "their reverse proxy". **Decided 2026-09-18 (Denis): two
names**, `cw.dentalia.si` for the office and `api.cw.dentalia.si` for machines
(webshop backend, BC item link, document files). The nested name follows the
server's own pattern (`demo.team`, `beta.team`).

How a request travels: DNS sends both names to `91.98.42.140`; the host Caddy
owns `:443`, reads the name the browser asked for (SNI and `Host`), and sends
both names to `127.0.0.1:8000`, passing `Host` through unchanged. That port is
our compose Caddy, published on loopback only, so the internet cannot reach it
except through theirs. Ours does the logins and, on the API name, refuses
everything but the machine paths (runbook § Web UI, "The API name"). TLS is
theirs; ours stays on `:8000` and must not be given a hostname.

**The change on the server.** One block, appended to `/etc/caddy/Caddyfile`,
touching nothing else in it:

```
cw.dentalia.si, api.cw.dentalia.si {
	log {
		output file /var/log/caddy/cw.log {
			roll_size 20mb
			roll_keep 5
			roll_keep_for 336h
		}
	}
	reverse_proxy 127.0.0.1:8000
}
```

Keep it bare: no `import dentalia_app` (their CSP, file server and redirects)
and no `basic_auth` (a second login layer breaks the BC link and API-key
callers). In order:

1. DNS: A records `cw` and `api.cw` in `dentalia.si` -> `91.98.42.140`. Done
   the same day: both resolve to `91.98.42.140` locally, at 1.1.1.1 and at
   8.8.8.8; no AAAA or CNAME on either and no CAA on `dentalia.si`, the same as
   `team.dentalia.si`, so Let's Encrypt issues as it does for theirs.
2. Our stack up; on the server `curl -I http://127.0.0.1:8000/healthz` -> 200.
3. `sudo cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak-$(date +%F)`
4. Append the block.
5. `sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile`
6. `sudo systemctl reload caddy`. **Never `restart`**: their unit reloads with
   `caddy reload --force`, which keeps the running config if the new one is
   refused, while a restart on a broken file takes their sites down.
7. Check: `https://cw.dentalia.si` -> 401 (our login), `https://api.cw.dentalia.si/`
   -> 404 `Not found`, `https://api.cw.dentalia.si/healthz` -> 200, and
   `https://team.dentalia.si` still 302.

On this host plain `caddy ...` and `systemctl ... caddy` mean **their** proxy;
ours is always `docker compose ... caddy`.

In our `.env`: `WEB_PUBLIC_BASE_URL=https://api.cw.dentalia.si`, and non-empty
`WEB_API_KEYS` and `WEB_BC_LINK_KEY`. The base is written into links stored in
BC, so it is fixed before the first BC push (deployment.md § 7).

This exposes the UI to the public internet behind Basic auth rather than to a
LAN, and rate limiting is still deferred (access spec § 4) — so the ingress
restriction below is what has to land, not an optional hardening step. Until it
does, reach the UI over an SSH tunnel from a trusted workstation rather than
over the public address.

### 6.2 BC routes (§§ 6.2, 6.4)

File routes only, per § 5.2.

### 6.3 Memory

No swap, 7.6 GiB, and a production app on the same kernel. Compose caps
`postgres` at 2 GiB (`mem_limit`), and `worker` has **no limit**. The worker's
peak under a large backfill (Chromium for the Playwright fetch tier, PyMuPDF
rasterising) has never been measured; the idle dev stack is about 230 MB. If the
kernel runs out of memory, the OOM killer picks by its own score and can kill
PocketBase rather than our worker. A `mem_limit` on `worker` in the prod
overlay (guide § 4) would contain that to our own container.

### 6.4 Log rotation

There's no `/etc/docker/daemon.json` and no `logging:` key in
`docker-compose.yml`, so every container logs to `json-file` with no size cap.
Our Caddy logs every request to stdout by design. On a shared 150 GB disk that
grows without bound. Either a `daemon.json` with `max-size`/`max-file` (needs
sudo and a Docker restart; that touches nothing of theirs, since PocketBase runs
under systemd, not in Docker), or a `logging:` block per service in the prod
overlay.

### 6.5 `docker-buildx`

Not installed. It has not been verified whether `docker compose build` under
Compose 2.40 works without it. `sudo apt install docker-buildx` is the cheap
way to avoid finding out mid-deploy.

### 6.6 Code delivery

The repository is private on GitHub over SSH. The server can reach `github.com`,
but no deploy key has been set up or tested. The alternatives are rsync from dev
or shipping built images.

**Recommended the same day:** a git checkout on the server with a read-only
GitHub deploy key, updated by `scripts/deploy.sh`, which is what the tooling
already assumed; Denis approved the three fixes below that it needs. If the code later moves to a repository of Dentalia's own: push
the history there, add a new read-only key on it (GitHub refuses one deploy key
on two repositories), and `git remote set-url origin` on the server; nothing
else changes. Three things landed the same day to make a build here match dev:
every package pinned in `constraints.txt` (until then only `playwright` was, so
a server build would have resolved whatever PyPI had that day), `deploy.sh`
hashing the checkout with the host's `python3` instead of the dev-only `test`
image, and a database dump plus a `backups/deploy.log` line before every
migration.

### 6.7 Directories

`/srv/compliance/` is the home for `PGDATA_HOST`, `ARCHIVE_HOST`,
`IMPORTS_HOST` and the checkout, alongside `/srv/dentalia/`. **Owner: `denis`**
(Denis, 2026-09-18). Re-read the same day it was still `root:root` 755, so
`sudo chown denis:denis /srv/compliance` is still to run before anything can be
put there without sudo.

## 7. Open decisions

1. Code delivery: confirm the deploy-key checkout recommended in § 6.6.

Decided the same day (Denis), all recorded in [decisions.md](decisions.md):
ingress is two names (§ 6.1); `/srv/compliance/` belongs to `denis` (§ 6.7);
`worker` is capped at 3g and every log rotates, through the shipped
`docker-compose.prod.yml` (§§ 6.3, 6.4); and migration 008 now leaves a set
`dentalia_api` password alone, so the one changed on this server survives
deploys (deployment.md § 3.3).

Later the same day `docker-buildx` was found installed (§ 6.5), by a
`dpkg-query` read as `denis`.

## 8. How to re-run

All read-only, as an ordinary login, no sudo:

```bash
ssh <user>@<host> 'bash -s' <<'EOF'
uname -m; . /etc/os-release; echo "$PRETTY_NAME"; nproc; free -h; df -hT /
id; passwd -S "$USER"; getent group sudo docker
dpkg-query -W -f='${Package} ${Version}\n' 'docker*' containerd runc
systemctl is-active docker containerd caddy pocketbase@prod pocketbase@demo
docker version --format '{{.Client.Version}} / {{.Server.Version}}'; docker compose version
docker info --format '{{.DockerRootDir}} {{.Driver}} {{.CgroupDriver}}/{{.CgroupVersion}} {{.LoggingDriver}}'
ls -l /etc/docker/daemon.json; docker ps -a
ss -tlnH | awk '{print $4}' | sort -u
ls -la /srv/ /srv/compliance/
for u in https://api.anthropic.com https://pypi.org/simple/ https://mcr.microsoft.com/v2/ \
         https://registry-1.docker.io/v2/ https://github.com https://api.search.brave.com; do
  printf '%-40s ' "$u"; curl -sS -o /dev/null -m 10 -w '%{http_code}\n' -I "$u"
done
getent hosts denwebnav || echo "denwebnav: no DNS"
EOF
```
