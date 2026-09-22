# Caddy logins

One file per deployment, one line per person:

```
maja $2a$14$...
tomaz $2a$14$...
```

Name it anything ending in `.caddy` (`people.caddy` is the convention) and put
it in this directory. `docker-compose.yml` mounts the directory read-only at
`/etc/caddy/users`, and the `Caddyfile` pulls every `*.caddy` file into its one
`basic_auth` block.

Generate a hash with:

```bash
docker compose run --rm caddy caddy hash-password --plaintext 'their-password'
```

Nothing here is committed except this file and `.gitignore`: the hashes are the
credential. An empty directory is fine -- the proxy then runs on the
`DENTALIA_WEB_USER` / `DENTALIA_WEB_PASSWORD_HASH` pair from `.env` alone.

Adding a person to `WEB_OPERATOR_USERS` is a separate step, and it is what
decides whether they may run the four operator-only actions. See
`docs/runbook.md` § Web UI.
