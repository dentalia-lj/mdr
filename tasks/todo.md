# BC login: NTLM inside Negotiate (2026-09-24)

Follow-up `[bc-auth-ntlm-in-negotiate]`. Status: **built and live-verified 2026-09-24.** Approved by Denis.

## What was measured (2026-09-24, from cw)

- BC answers `401` with `WWW-Authenticate: Negotiate` only.
- Basic: refused. Bare `Authorization: NTLM`: ignored, no challenge.
- NTLM token sent as `Authorization: Negotiate <ntlm>` on one keep-alive
  connection: challenge, then `200` on `allitems?$top=1` as
  `DENTALIA3\dentalia.mdr` (pyspnego, `protocol="ntlm"`).
- Plain HTTP is the rule (decisions 2026-09-24), not a gap.

## Change

- [x] `pyproject.toml`: `pyspnego` in the `ingest` extra (the worker and the
      test image install it; the web image does not construct a BC client).
      `constraints.txt`: pin `pyspnego==0.12.2`; `cryptography` is already pinned.
- [x] `app/adapters/bc_client.py`: replace httpx Basic with an `httpx.Auth`
      subclass. Request goes out plain; on `401` + `Negotiate`, run the NTLM
      handshake on the same connection (type 1 -> challenge -> type 3) and
      resend. Basic is removed, not kept behind a switch: the server refuses it.
      No credentials -> no `Authorization` header, as today.
- [x] A `401` AFTER a completed handshake raises `BcAuthRejected` and the pair
      is remembered per process, so retries never resend it (see risk 1). The
      queue has no don't-retry signal, so the guard lives in the client.
- [x] `tests/test_bc_client.py`: a fake transport that plays the server side
      with `spnego.server(protocol="ntlm")` and a user file, so the handshake is
      real, not scripted. Cases: success; wrong password -> distinct error; no
      credentials -> no header; PATCH body survives the resend; a server that
      stays authenticated on the connection costs one round trip, not three.
- [x] Docs, same session: `bc_client.py` docstring, `app/config.py` comment,
      `docs/dev/config-reference.md` (username is `DOMAIN\user`, quoting in
      `.env`), `docs/dev/limits.md`, `docs/troubleshooting.md` (the
      "denwebnav not reachable" entry), `docs/dev/deployment.md` examples
      (`denwebnav` -> `mail.dentalia.si`), `docs/code-map.md` row.
- [x] Rebuild the worker and test images (dependency set changed). **Done
      2026-09-25**: `./scripts/deploy.sh` at `d19f9c1` (verified, no drift,
      dump `backups/20260925T075556Z-ran-c1476c0.dump`), then the `test` image.
      `pyspnego` imports in both; the worker carries its 5 `BC_*` keys.
- [x] Tests: `./scripts/test.sh tests/test_bc_client.py tests/test_ingest*.py`,
      then the full suite once before commit.
- [x] Live check on cw with the new client (Denis ran it, 2026-09-24): first
      GET 3 requests (bare, Negotiate, Negotiate) in 1.05s; every later page 1
      request, so BC keeps the connection authenticated. `allitems?$top=100`
      7.09s, `dataitems?$top=100` 0.13s. `$metadata`: `dataitem` key `no`.

## Risks

1. **Account lockout.** `DENTALIA3\dentalia.mdr` is a domain account. A wrong
   or rotated password retried by job backoff and the hourly `bc.push-drift`
   cron could lock it, and that locks whatever else uses it. Hence the
   no-retry error above.
2. **Connection-bound auth.** NTLM authenticates the TCP connection. httpx
   reuses the idle connection for the same origin in a sync client; the tests
   pin this, and the live check confirms it.
3. **Backslash in `.env`.** `BC_USERNAME=DENTALIA3\dentalia.mdr` must survive
   compose's `.env` parsing. Single quotes are the safe form; to be verified,
   not assumed.

## Out of scope (already tracked)

- `BC_*` does not reach the containers: `[config-placement-decided]`.
- `bc.push` with `BC_WRITE_ENABLED` on would call `client.patch` on `None`:
  nothing constructs its client. Logged as `[bc-push-has-no-client]`.

---

# `bc.push`: make the write-back actually write (2026-09-24)

Follow-up `[bc-push-has-no-client]`. Status: **Step 1 and option B built 2026-09-24, full suite 4202 passed. Write stays OFF -- Denis turns it on later; Step 2 waits for that.**
Login and wiring are done (above; `BC_*` on `worker` since 2026-09-24).

## What is broken today (verified in the tree)

1. Nothing builds a client: `handle_bc_push(conn, job, *, client=None)`, and the
   runner never passes one. With writes on, `client.patch` on `None` -> dead job.
2. The PATCH URL is the bare item number: `client.patch(item_ref, changed)`.
3. No `If-Match`. BC API pages normally refuse a PATCH without one.
4. Rollout has no brake: `BC_WRITE_ENABLED=true` switches on the hourly
   `bc.push-drift` cron at the same moment, up to `BC_DRIFT_CAP` (1000) items
   per run, and the ledger is empty (0 rows), so every item is "changed".

## Facts from the live read (2026-09-24)

- `dataitem` key is `no`; 5 properties; each row has `@odata.etag`.
- 3.195 of 15.958 item refs need escaping in a key: `/` 2.019, space 1.618,
  `+` 13, `,` 6, `#` 3, `Č` 2, `*` 2. A `/` encoded as `%2F` is often refused
  by IIS/HTTP.sys -- unknown for this server.

## Step 0 -- read-only probes on cw (Denis runs, no code change)

- [x] `$count`: 19.357 on BOTH `allitems` and `dataitems` -- not a subset.
      Our mirror has 15.958 (`[mirror-behind-bc]`).
- [x] GET `dataitems('<ref>')` fully percent-encoded (`quote(safe="")`, `'`
      doubled) returns 200 for plain, space, `/` (`%2F`), `Č`, `#`. The
      `unaddressable` fallback below is therefore NOT built.

## Step 1 -- code

- [x] `bc_push.py`: build `BcClient(cfg.bc.base_url, ...)` when none is
      injected and `write_enabled` (same shape as `ingest.py:139`); close it in
      `finally`. Not built when withheld, so a preview never logs in.
- [x] `bc_client.py`: `patch_item(no, fields)` builds
      `{base}/dataitems('{no}')`: `'` doubled (OData literal), then
      percent-encoded; sends `If-Match: *`.
      Why `*` and not the row ETag: our ledger is the diff source and BC is
      never read back (design § 4); an ETag needs a GET per item, doubling the
      requests. `*` means a human edit to these three fields in BC is
      overwritten on the next push -- which is the ownership the design gives
      us. **Decision for Denis.**
- [x] ~~Encoding outcome of Step 0 decides the fallback~~ not needed: every shape addressable for refs BC will not
      address: counted and sampled as `unaddressable`, never retried blind.
- [x] `BcAuthRejected` fails the whole job (not per item): one refused login
      must not become 200 more attempts in the same run.
- [x] Tests (fake BC, `tests/test_bc_push.py` + `test_bc_client.py`): URL for
      each special character, `If-Match` sent, client built only when writing,
      auth refusal stops the batch, 412/428 counted as `refused`.
- [x] Docs: `handlers.md` (`bc.push`), `limits.md` "No HTTP client" row
      deleted, `troubleshooting.md` NoneType entry deleted, `PHASES.md` item 5.

## Step 2 -- one supervised live write (Denis picks the item)

- [ ] GET the item's three fields (before), run the handler inline in a
      one-off container with `-e BC_WRITE_ENABLED=true` (the job runs in THAT
      process, so the global switch stays off), GET again (after), then
      revert to the before-values if Denis wants it left untouched.

## Step 3 -- rollout (Denis decides)

- [ ] ~~Option A~~: flip `BC_WRITE_ENABLED=true`; drift fills BC at 1000 items/h,
      about 16 h for the catalogue. Bulk preview first to eyeball the numbers.
- [x] Option B (chosen): add `SCHEDULER_BC_PUSH_DRIFT_ENABLED` (default false), so
      writes can be on for the button and the bulk apply while drift stays off
      until the first bulk run has been checked. One key, same pattern as every
      other cron.
