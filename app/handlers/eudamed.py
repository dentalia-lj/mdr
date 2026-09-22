"""`eudamed.sync` — one Basic UDI-DI, one call, rows in `eudamed_mirror`.

    eudamed.sync {basic_udi_di}
      -> GET /devices/udiDiData?basicUdi=<value>&size=100&page=N, N until last
      -> upsert one eudamed_mirror row per device in that group

**Stage 1 only** (Denis, 2026-08-25). This writes `eudamed_mirror` and nothing
else. No `item_document`, no `document`, no `evidence` — invariant 1 holds
trivially, and `tests/test_eudamed_handler.py` asserts it rather than trusting
it. The `ref-eudamed` match basis is capped at `staged` by the same ruling; no
code here creates one.

**Why bother.** Every EUDAMED device record carries `reference`, its
Reference/Catalogue number (MDR Annex VI Part B), populated on 100% of the
16.502 records swept on 2026-08-20. That is the article number a document
actually covers — the article-level evidence 65,8% of our covered devices lack.
A worked example from that sweep: `basicUdi=471070188CNQQ` returns 41 devices,
each with its own reference (`7031`, `7024`, … `601`).

**Why this does not light up DISCOVER.** Its eudamed rung reads `cert_refs`,
i.e. document URLs, and the same research measured that EUDAMED publishes none.
Filling this mirror leaves that rung missing exactly as it was. Pinned by a test
so nobody later files it as a regression.

**Groups are far bigger than the first measurement suggested.** The
2026-08-20 sweep put the largest at 41 devices; our own catalogue holds
`76152082APROS004VZ` at **7.586**, in 76 pages. That matters twice: a size
threshold is worthless as a filter-integrity guard (`_assert_filtered` does that
job by checking the group each record actually belongs to), and one button press
on a large group is dozens of polite-rate requests.

**The response is paged, and the default page is 20.** `/devices/udiDiData`
answers with a Spring page envelope (`content`, `number`, `size`,
`totalElements`, `totalPages`, `last`). The research doc's own worked example
returns 47 devices in 3 pages, so a handler that read one response stored 20 of
them and reported `devices: 20` -- a silent 57% loss that reads as a clean hit.
This walks to `last`. `size=100` and `page=N` were both confirmed live
2026-08-25, which is not a formality on an API where unknown parameters are
silently ignored.

**The API is undocumented and unofficial** (base `https://ec.europa.eu/tools/
eudamed/api`, endpoints read out of the public UI bundle and then probed).
Unknown parameters are *silently ignored* and return the unfiltered 3.19M-record
set, so a typo in the parameter name does not error — it returns everything and
reads as a spectacular hit. `basicUdi` is the one confirmed to filter;
`manufacturerName`, `manufacturerSrn`, `deviceName` and `nomenclatureCode` all
look plausible and do nothing.

Fetching goes through the `Fetcher` adapter, never raw httpx (invariant 11), so
this inherits the same transport, user agent and politeness posture as FETCH.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
from urllib.parse import quote

from app.adapters.fetcher import make_fetcher
from app.config import load_config
from app.eudamed_names import EXACT_VIA, match_actor
from app.handlers import register
from app.results import Result

log = logging.getLogger("dentalia.handler.eudamed")

#: Public EUDAMED device search. Base and parameter both measured 2026-08-20;
#: see the module docstring on why the parameter name is load-bearing.
EUDAMED_DEVICE_URL = (
    "https://ec.europa.eu/tools/eudamed/api/devices/udiDiData"
    "?basicUdi={basic_udi}&size={size}&page={page}"
)

#: Bigger than the default 20, so every group we have measured comes back in one
#: call. A courtesy to EUDAMED, never the correctness argument -- the walk below
#: is what makes the read complete.
PAGE_SIZE = 100

#: Backstop, not a judgement about group size. The first version put this at
#: 500 on the 2026-08-20 sweep's "41 at its largest" and it refused a real
#: answer within six button presses: Ivoclar's `76152082ACERA011ET` is 223
#: devices and `76152082APROS004VZ` is **7.586**, every record carrying that
#: exact `basicUdi` and one manufacturer name. Group size is simply not a signal
#: about whether the filter held -- `_assert_filtered` is. What is left here
#: only separates "a very large group" from "the unfiltered 3.19M set".
#: Counted across the whole walk; per page it would pass an unfiltered query
#: through in slices.
MAX_DEVICES = 50_000

#: Second belt on the same failure. `MAX_DEVICES` catches a runaway returning
#: full pages; this catches one returning thin ones that never says `last`.
#: 76 pages is a group we actually hold, so this has to clear that comfortably.
MAX_PAGES = MAX_DEVICES // PAGE_SIZE + 10

_UPSERT = """
INSERT INTO eudamed_mirror (udi_di, basic_udi_di, device_name, trade_name,
                            manufacturer_srn, reference, synced_at)
VALUES (%s, %s, %s, %s, %s, %s, now())
ON CONFLICT (udi_di) DO UPDATE SET
    basic_udi_di     = EXCLUDED.basic_udi_di,
    device_name      = EXCLUDED.device_name,
    trade_name       = EXCLUDED.trade_name,
    manufacturer_srn = EXCLUDED.manufacturer_srn,
    reference        = EXCLUDED.reference,
    synced_at        = now()
"""


def _devices(payload) -> list[dict]:
    """EUDAMED pages its device list under `content`. Tolerate a bare list."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("content") or []
    return []


def _assert_filtered(devices, basic_udi_di: str) -> None:
    """Refuse an answer that is not about the group we asked for.

    This is the guard that knows something. Unknown parameters on this API are
    silently ignored and return the unfiltered 3.19M-record set, and the exact
    signal of that is records belonging to other groups -- not a record count,
    which can only guess and guessed wrong the first time (see `MAX_DEVICES`).

    A record that omits `basicUdi` is taken at the query's word: EUDAMED
    answered a filtered request, and not echoing the field back is absence, not
    contradiction.
    """
    for d in devices:
        got = (d.get("basicUdi") or "").strip()
        if got and got != basic_udi_di:
            raise RuntimeError(
                f"EUDAMED returned a device in group {got!r} for a query on "
                f"{basic_udi_di!r} — the query stopped filtering"
            )


def _pause_between_pages() -> None:
    """Spend `cfg.fetch.politeness_ms` between pages of a walk.

    The paging loops fetched back to back with no delay at all -- a 38-page
    IVOCLAR sweep is 38 requests to `ec.europa.eu` as fast as the network
    allows. On 2026-09-02 that host began answering HTTP 307 with an HTML
    "Network Error" page, to the host and the worker container alike, after a
    couple of clean rounds, and recovered on its own once the requests stopped.
    That is rate-based throttling, not a block, and the empty-`content` page
    `_is_last` now rejects looks like its softer first stage.

    FETCH spends this same budget through the domain lease, but a multi-page
    walk inside ONE job cannot: the lease path DEFERS the job when the lease is
    held (`app/handlers/fetch.py`), and this handler runs its whole walk in one
    transaction, so deferring mid-walk would discard every page already stored.
    An in-process pause is the honest version of the same politeness -- it is
    what makes the walk slow rather than what makes it restartable, and the
    restartable version is `[eudamed-sweep-is-one-long-transaction]`.

    Read per call rather than cached so an operator raising the config value
    does not need a restart to slow a sweep down.
    """
    time.sleep(load_config().fetch.politeness_ms / 1000.0)


def _is_last(payload, devices) -> bool:
    """Whether the walk ends here.

    Two ways to stop, and the second is not redundant: `last` is EUDAMED's word
    and a bare list or a malformed envelope carries none, so an empty page ends
    the walk regardless. Without that, a body we cannot read pages forever.

    The exception is an empty page that CONTRADICTS its own envelope, which is
    a throttle wearing a 200. Reproduced live 2026-09-02 by interleaving the
    sweep's own URL across the host and the worker container: one round
    returned a valid Spring envelope whose `content` was `[]`, and the rounds
    after it returned HTTP 307 with an HTML "Network Error" page from BOTH
    positions in the same second. EUDAMED degrades to an empty page before it
    degrades to a block, and the pre-guard reading of that page as a clean end
    of pagination is how jobs 35679 and 35701 swept IVOCLAR on page 0, stored
    nothing, and reported success.

    The envelope tells the two apart without guessing. A real ending agrees
    with itself -- `last: true`, or nothing registered at all -- while a
    throttled page claims 11.366 elements across 38 pages and then hands over
    none of them. Raising here rather than returning False is deliberate: a
    retry of the same page is what the caller must NOT do unattended, because
    hammering is what produced the throttle. The job fails, backs off, and the
    empty-sweep guard downstream keeps the manufacturer on the due list.
    """
    if not devices:
        if isinstance(payload, dict) and _contradicts_empty(payload):
            raise RuntimeError(
                "EUDAMED returned an empty page that contradicts its own "
                f"envelope (totalElements={payload.get('totalElements')!r}, "
                f"totalPages={payload.get('totalPages')!r}, "
                f"number={payload.get('number')!r}, "
                f"last={payload.get('last')!r}) — treating a throttled "
                "response as the end of the walk would silently truncate it"
            )
        return True
    if isinstance(payload, dict) and "last" in payload:
        return bool(payload["last"])
    return True


def _contradicts_empty(payload: dict) -> bool:
    """Does this envelope say there was more to come?

    Read defensively -- a malformed or partial body must fall through to the
    old behaviour (end the walk) rather than raise, which is the posture the
    rest of this module keeps. Only a body that positively asserts more work
    counts as a contradiction.
    """
    if payload.get("last") is False:
        return True
    total = payload.get("totalElements")
    if isinstance(total, int) and total > 0:
        number, pages = payload.get("number"), payload.get("totalPages")
        if isinstance(number, int) and isinstance(pages, int):
            return number < pages - 1
        return True
    return False


def handle_eudamed_sync(conn, job, *, fetcher=None) -> dict:
    payload = job.get("payload") or {}
    basic_udi_di = (payload.get("basic_udi_di") or "").strip()
    r = Result()

    # Most documents carry no Basic UDI-DI (301 of 769). The button is not
    # offered for them; a hand-enqueued job must still not dead-letter.
    if not basic_udi_di:
        r.count("skipped_no_basic_udi_di")
        r.note("no basic_udi_di on the payload — nothing to look up")
        return r.as_dict()

    if fetcher is None:
        fetcher = make_fetcher()

    quoted = quote(basic_udi_di, safe="")
    devices: list[dict] = []
    for page in range(MAX_PAGES + 1):
        if page == MAX_PAGES:
            raise RuntimeError(
                f"EUDAMED never reported a last page for {basic_udi_di} — "
                f"stopped paging after {MAX_PAGES} pages"
            )
        if page:
            _pause_between_pages()
        resp = fetcher.get(EUDAMED_DEVICE_URL.format(
            basic_udi=quoted, size=PAGE_SIZE, page=page,
        ))

        # Raise rather than report: the queue owns backoff and the dead-letter,
        # and a swallowed 503 would report success for a group never read.
        if resp.status != 200:
            raise RuntimeError(f"EUDAMED returned {resp.status} for {basic_udi_di}")

        try:
            body = json.loads(resp.body or b"")
        except (ValueError, TypeError) as exc:
            raise RuntimeError(
                f"EUDAMED returned a non-JSON body for {basic_udi_di}"
            ) from exc

        got = _devices(body)
        _assert_filtered(got, basic_udi_di)
        devices.extend(got)
        if len(devices) > MAX_DEVICES:
            raise RuntimeError(
                f"EUDAMED returned {len(devices)} devices for {basic_udi_di} — "
                "past the plausible group size, so the query stopped filtering"
            )
        if _is_last(body, got):
            break

    for d in devices:
        udi_di = (d.get("primaryDi") or "").strip()
        if not udi_di:
            # udi_di is the primary key; a record without one cannot be stored.
            r.count("skipped_no_udi_di")
            continue
        reference = (d.get("reference") or "").strip() or None
        if reference is None:
            r.count("missing_reference")
        conn.execute(_UPSERT, (
            udi_di,
            (d.get("basicUdi") or basic_udi_di).strip() or None,
            (d.get("deviceName") or "").strip() or None,
            # `deviceName` was null on all 121 records measured 2026-08-25;
            # `tradeName` is what actually carries the name. Both stored --
            # see migration 039 on why they are not coalesced here.
            (d.get("tradeName") or "").strip() or None,
            (d.get("manufacturerSrn") or "").strip() or None,
            reference,
        ))
        r.count("devices")
        r.sample("devices", {"udi_di": udi_di, "reference": reference})

    if not devices:
        # 52,3% of our Basic UDI-DIs resolve to nothing (measured 2026-08-20).
        # The common case, not a failure.
        r.note(f"EUDAMED holds no device for {basic_udi_di}")

    return r.as_dict()


register("eudamed.sync", handle_eudamed_sync)


#: Public EUDAMED certificate search. Unfiltered on purpose: the whole register
#: is 4.608 records in 16 calls (measured 2026-08-26), `actorSrn` arrives on
#: every one, and matching locally avoids the endpoint's substring matcher --
#: `actorName=Ivo` returns 4 records, `actorName=Ivoclar` returns 1.
EUDAMED_CERT_URL = (
    "https://ec.europa.eu/tools/eudamed/api/certificates/search/"
    "?size={size}&page={page}"
)

#: The server's cap. Asking for 1.000 returns 300. At the default 20 the
#: register would be 231 calls instead of 16.
CERT_PAGE_SIZE = 300

#: A dedicated backstop, not a reuse of `MAX_PAGES` -- that constant is sized
#: for `PAGE_SIZE` (100), and this walk pages at `CERT_PAGE_SIZE` (300).
#: Reusing it here would not fire until roughly 153.000 records, about 3x
#: looser than `MAX_DEVICES` intends -- the same argument `SWEEP_MAX_PAGES`
#: makes below for the per-manufacturer walk.
CERT_MAX_PAGES = MAX_DEVICES // CERT_PAGE_SIZE + 10

_CERT_UPSERT = """
INSERT INTO eudamed_certificate
  (certificate_number, revision_number, actor_srn, actor_name,
   certificate_type, certificate_status, issue_date, starting_validity,
   expiry_date, notified_body_srn, version_number, synced_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
ON CONFLICT (certificate_number, revision_number, actor_srn) DO UPDATE SET
    actor_name         = EXCLUDED.actor_name,
    certificate_type   = EXCLUDED.certificate_type,
    certificate_status = EXCLUDED.certificate_status,
    issue_date         = EXCLUDED.issue_date,
    starting_validity  = EXCLUDED.starting_validity,
    expiry_date        = EXCLUDED.expiry_date,
    notified_body_srn  = EXCLUDED.notified_body_srn,
    version_number     = EXCLUDED.version_number,
    synced_at          = now()
-- first_seen is deliberately absent: a new revision is a new row, so an
-- untouched first_seen IS the renewal signal.
"""

#: `status` and `decided_at` are absent from the DO UPDATE set on purpose: the
#: register is re-pulled monthly, and a rejected candidate that came back as
#: `pending` every month would be a queue nobody can clear.
_SRN_UPSERT = """
INSERT INTO manufacturer_srn
  (canonical_name, srn, actor_name, discovered_via, status, match_score)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (canonical_name, srn) DO UPDATE SET
    actor_name  = EXCLUDED.actor_name,
    match_score = EXCLUDED.match_score
"""

#: RULING 8 (Denis, load-bearing, not in the task-4 brief). Migration 042's
#: `manufacturer` seed is a one-shot that runs at migrate time against
#: `manufacturer_alias`; nothing populates `manufacturer_alias` in a migration
#: -- RESOLVE fills it at runtime -- so on a freshly created database the seed
#: inserts zero rows, permanently. `manufacturer_srn.canonical_name` carries a
#: FK to `manufacturer(canonical_name)`, so without this upsert immediately
#: before every `_SRN_UPSERT`, a canonical name known only through
#: `manufacturer_alias` raises a foreign-key violation instead of degrading.
_MANUFACTURER_UPSERT = """
INSERT INTO manufacturer (canonical_name) VALUES (%s)
ON CONFLICT (canonical_name) DO NOTHING
"""


def _code(value):
    """EUDAMED wraps enums as {"code": "refdata.<namespace>.<value>"}. The
    namespace is reference data, not information."""
    if not isinstance(value, dict):
        return None
    code = value.get("code")
    return code.rsplit(".", 1)[-1] if code else None


def _day(value):
    """Dates arrive as `2031-05-04T00:00:00`. Postgres takes the date half.

    A value that is not a date is DROPPED, not truncated. Slicing blindly turned
    `not-a-real-date-value` into `not-a-real`, which Postgres rejects with
    InvalidDatetimeFormat -- and because `app/workers/runner.py:53-60` wraps the
    whole handler in ONE transaction, that rolls back every certificate and SRN
    row the run had already written and dead-letters the entire 4.608-record
    pull. One bad field must cost one field, not the register.
    """
    if not isinstance(value, str) or not value:
        return None
    head = value[:10]
    try:
        dt.date.fromisoformat(head)
    except ValueError:
        return None
    return head


def _candidate_aliases(conn) -> dict:
    """canonical name -> every raw alias that resolves to it, plus the canonical
    string itself. KOMET trades as Gebr. Brasseler, which is the actor name
    carrying its certificate, so matching on the canonical string alone would
    miss it."""
    out: dict[str, list[str]] = {}
    for row in conn.execute(
        "SELECT canonical_name, raw_name FROM manufacturer_alias"
    ).fetchall():
        out.setdefault(row["canonical_name"], []).append(row["raw_name"])
    for row in conn.execute(
        "SELECT canonical_name FROM manufacturer"
    ).fetchall():
        out.setdefault(row["canonical_name"], []).append(row["canonical_name"])
    return out


def handle_eudamed_certregister(conn, job, *, fetcher=None) -> dict:
    """Mirror the whole EU certificate register, then attribute actors locally.

    Writes `eudamed_certificate` and `manufacturer_srn`. Writes no registry row
    -- Invariant 1, asserted by test.
    """
    r = Result()
    if fetcher is None:
        fetcher = make_fetcher()

    records: list[dict] = []
    for page in range(CERT_MAX_PAGES + 1):
        if page == CERT_MAX_PAGES:
            raise RuntimeError(
                "EUDAMED never reported a last page for the certificate "
                f"register -- stopped paging after {CERT_MAX_PAGES} pages"
            )
        if page:
            _pause_between_pages()
        resp = fetcher.get(EUDAMED_CERT_URL.format(
            size=CERT_PAGE_SIZE, page=page,
        ))
        if resp.status != 200:
            raise RuntimeError(
                f"EUDAMED returned {resp.status} for the certificate register"
            )
        try:
            body = json.loads(resp.body or b"")
        except (ValueError, TypeError) as exc:
            raise RuntimeError(
                "EUDAMED returned a non-JSON body for the certificate register"
            ) from exc

        got = _devices(body)
        records.extend(got)
        if _is_last(body, got):
            break

    for c in records:
        number = (c.get("certificateNumber") or "").strip()
        actor = (c.get("actorSrn") or "").strip()
        if not number or not actor:
            # Both are primary-key components; a record missing either cannot
            # be stored. Counted, never silent (CLAUDE.md).
            r.count("skipped_incomplete")
            continue
        dates = {}
        for field, key in (("issue_date", "issueDate"),
                           ("starting_validity", "startingValidityDate"),
                           ("expiry_date", "expiryDate")):
            raw = c.get(key)
            dates[field] = _day(raw)
            # Present but unreadable is a data-quality signal worth surfacing;
            # absent is the ordinary case and is not counted.
            if raw and dates[field] is None:
                r.count("unparseable_date")
        conn.execute(_CERT_UPSERT, (
            number,
            (c.get("revisionNumber") or "").strip(),
            actor,
            (c.get("actorName") or "").strip() or None,
            _code(c.get("certificateType")),
            _code(c.get("certificateStatus")),
            dates["issue_date"],
            dates["starting_validity"],
            dates["expiry_date"],
            (c.get("notifiedBodySrn") or "").strip() or None,
            c.get("versionNumber"),
        ))
        r.count("certificates")

    candidates = _candidate_aliases(conn)
    seen: set[tuple[str, str]] = set()
    for c in records:
        actor = (c.get("actorSrn") or "").strip()
        actor_name = (c.get("actorName") or "").strip()
        if not actor or not actor_name or (actor, actor_name) in seen:
            continue
        seen.add((actor, actor_name))
        canonical, via, score = match_actor(actor_name, candidates)
        if canonical is None:
            r.count("unmatched_actors")
            continue
        status = "auto" if via == EXACT_VIA else "pending"
        # RULING 8: guarantee the FK target exists before writing the SRN row.
        conn.execute(_MANUFACTURER_UPSERT, (canonical,))
        conn.execute(_SRN_UPSERT, (
            canonical, actor, actor_name, via, status, score,
        ))
        r.count("srn_auto" if status == "auto" else "srn_pending")
        r.sample("srns", {"canonical": canonical, "srn": actor, "via": via})

    return r.as_dict()


register("eudamed.certregister", handle_eudamed_certregister)


#: Per-manufacturer device catalogue. `srn=` is the one parameter here that
#: cannot return another company's devices -- `reference=` and `actorName=` are
#: both substring matchers. Confirmed exact 2026-08-26: srn=DE-MF-000005066
#: returns exactly 3.594 devices, matching the independent Carl Martin count.
EUDAMED_SRN_URL = (
    "https://ec.europa.eu/tools/eudamed/api/devices/udiDiData"
    "?srn={srn}&size={size}&page={page}"
)

#: The devices endpoint honours 300 too (measured 2026-08-26), which makes
#: Ivoclar ~38 pages instead of the 114 the first design draft assumed.
SWEEP_PAGE_SIZE = 300

#: The article-probe fallback's own query variant. `EUDAMED_SRN_URL` set the
#: precedent -- a distinct parameter gets its own named constant, so a grep for
#: the endpoint finds every caller. String surgery on another constant (e.g.
#: `.replace()`-ing `basicUdi=` into `reference=`) breaks silently if that base
#: string is ever reworded.
EUDAMED_REF_URL = (
    "https://ec.europa.eu/tools/eudamed/api/devices/udiDiData"
    "?reference={reference}&size={size}&page={page}"
)

#: A dedicated backstop, not a reuse of `MAX_PAGES` -- that constant is sized
#: for `PAGE_SIZE` (100), and this walk pages at `SWEEP_PAGE_SIZE` (300).
#: Reusing it here would not fire until roughly 153.000 devices, about 3x
#: looser than `MAX_DEVICES` intends. `_assert_srn` is the real
#: filter-failure defence and fires on the first bad record; this only
#: separates "a very large catalogue" from "a query that kept filtering but
#: never said `last`".
SWEEP_MAX_PAGES = MAX_DEVICES // SWEEP_PAGE_SIZE + 10

_MIRROR_SWEEP_UPSERT = """
INSERT INTO eudamed_mirror (udi_di, basic_udi_di, device_name, trade_name,
                            manufacturer_srn, reference, device_status_type,
                            synced_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, now())
ON CONFLICT (udi_di) DO UPDATE SET
    basic_udi_di       = EXCLUDED.basic_udi_di,
    device_name        = EXCLUDED.device_name,
    trade_name         = EXCLUDED.trade_name,
    manufacturer_srn   = EXCLUDED.manufacturer_srn,
    reference          = EXCLUDED.reference,
    device_status_type = EXCLUDED.device_status_type,
    synced_at          = now()
-- first_seen absent on purpose: it is the sweep delta.
"""


def _assert_srn(devices, srn: str) -> int:
    """Layer 3 of the wrong-attribution mitigation (spec §5.2).

    Unknown parameters are silently ignored by this API and return the
    unfiltered 3,25M-record set rather than erroring, so a renamed parameter
    reads as a spectacular hit. A record carrying a DIFFERENT srn is exactly
    that signal and must raise.

    A record that omits `manufacturerSrn` is a different case, in the same
    shape `_assert_filtered` already uses for `basicUdi`: EUDAMED answered a
    filtered request, and not echoing the field back is absence, not
    contradiction. This is not a hypothetical on an API this undocumented --
    the same module measured `deviceName` null on 121 of 121 records and
    `revisionNumber` null on 481 of 4.608. Raising on absence would abort the
    whole sweep (`app/workers/runner.py` wraps the handler in ONE
    transaction, so every SRN and every page already fetched rolls back) over
    a field nobody has measured the drop rate of. The upsert already stores
    the REQUESTED srn regardless of what the record echoes, so refusing to
    store here would be inconsistent with how the row is actually written.

    Returns the count of records that did not echo `manufacturerSrn` at all,
    so the caller can report it as `srn_not_echoed` -- counted, never silent
    (CLAUDE.md).
    """
    not_echoed = 0
    for d in devices:
        got = (d.get("manufacturerSrn") or "").strip()
        if not got:
            not_echoed += 1
            continue
        if got != srn:
            raise RuntimeError(
                f"EUDAMED returned a device for {got} when asked "
                f"for {srn} -- the srn filter is not holding, refusing to store"
            )
    return not_echoed


def probe_srn(conn, canonical_name: str, *, fetcher) -> str | None:
    """Last resort: read an SRN off one of our own article numbers.

    Only reached for a device manufacturer holding no notified-body
    certificate -- GC EUROPE, 356 device articles and zero certificates
    (measured 2026-08-26), so its SRN can never come from
    `eudamed.certregister`'s pull of the certificate register.

    Both gates below are mandatory, independently, not redundantly.
    `reference=` is a substring matcher (`reference=64` -> 62.918 records, an
    ungated sample returning Promedics Orthopaedics, PAUL HARTMANN and
    Cerascreen records for Dentalia article numbers), so an exact-reference
    check alone is not enough. Article numbers are also not globally unique
    across manufacturers, so a name check alone is not enough either. Only a
    record that clears both -- the exact article AND a manufacturer name that
    resolves, EXACTLY, to this same canonical manufacturer -- can bootstrap an
    SRN; either gate failing alone must refuse.

    Known limitation, left as-is rather than fixed here: this probe reads page
    0 only, one call. For a very short article number the exact match can sit
    beyond page 0 of a many-thousand-record substring hit, and the probe then
    returns None. That fails SAFE -- it never returns a wrong SRN, because both
    gates still have to pass on whatever page 0 happens to hold -- but it still
    stamps `srn_probed_at`, so that manufacturer will not be re-probed until an
    operator clears the stamp. Paging this to chase a match on a later page is
    deliberately not done; one call is the design, not an oversight.
    """
    # RULING 8 (see _MANUFACTURER_UPSERT above, task 4): the same one-shot seed
    # gap applies here. `manufacturer` is populated once, at migrate time, from
    # `manufacturer_alias`; nothing re-runs that seed afterwards, so a
    # canonical name known only through `manufacturer_alias` has no
    # `manufacturer` row on a fresh environment. Without this upsert FIRST, the
    # `UPDATE ... SET srn_probed_at` below would affect zero rows -- the probe
    # would never be recorded and would repeat forever -- and a successful
    # match's `INSERT INTO manufacturer_srn` would raise a foreign-key
    # violation instead of degrading.
    conn.execute(_MANUFACTURER_UPSERT, (canonical_name,))

    row = conn.execute(
        "SELECT im.item_ref, im.mfr_ref FROM item_mirror im "
        "  JOIN item_group_member gm ON gm.item_ref = im.item_ref "
        "  JOIN item_group g ON g.group_id = gm.group_id "
        " WHERE g.canonical_manufacturer = %s AND im.md_flag "
        " ORDER BY im.item_ref LIMIT 1", (canonical_name,)).fetchone()

    # Stamped unconditionally from here on, whether or not a candidate article
    # exists and whether or not the fetch below finds a match -- see the
    # docstring's "known limitation" and `manufacturer.srn_probed_at`'s own
    # column comment (migration 042) on why "probed, found nothing" must be
    # distinguishable from "never probed".
    conn.execute(
        "UPDATE manufacturer SET srn_probed_at = now() WHERE canonical_name = %s",
        (canonical_name,))
    if not row:
        return None

    article = (row["mfr_ref"] or row["item_ref"] or "").strip()
    if not article:
        return None

    resp = fetcher.get(EUDAMED_REF_URL.format(
        reference=quote(article, safe=""), size=PAGE_SIZE, page=0))
    if resp.status != 200:
        raise RuntimeError(
            f"EUDAMED returned {resp.status} probing {canonical_name}")
    try:
        body = json.loads(resp.body or b"")
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            f"EUDAMED returned a non-JSON body probing {canonical_name}"
        ) from exc

    candidates = _candidate_aliases(conn)
    for d in _devices(body):
        if (d.get("reference") or "").strip() != article:
            continue                      # gate 1: exact reference
        name = (d.get("manufacturerName") or "").strip()
        matched, via, _ = match_actor(
            name, {canonical_name: candidates.get(canonical_name, [])})
        if matched != canonical_name or via != EXACT_VIA:
            continue                      # gate 2: manufacturer corroborates
        srn = (d.get("manufacturerSrn") or "").strip()
        if not srn:
            continue
        conn.execute(
            "INSERT INTO manufacturer_srn (canonical_name, srn, actor_name, "
            "  discovered_via, status, probe_ref) "
            "VALUES (%s, %s, %s, 'article-probe', 'auto', %s) "
            "ON CONFLICT (canonical_name, srn) DO NOTHING",
            (canonical_name, srn, name, article))
        return srn
    return None


def _clear_release(conn, manufacturer: str) -> None:
    """The sweep is over for this manufacturer, one way or another -- let go
    of the claim so it is visible again.

    `eudamed_sweep_due` and `_tick_eudamed_sweep_due` (app/scheduler.py) both
    exclude `released_at IS NOT NULL`, and only the successful-sweep exit
    below (the `eudamed_sweep_state` upsert at the end of this function) ever
    wrote NULL back into it. Every earlier return left a released row claimed
    forever -- invisible to the sweep-due list, with no dead job and nothing
    to re-run (final review, important #3). `due_at` is deliberately left
    untouched here: it is already in the past for anything that was on the
    due list, so leaving released_at NULL is what surfaces the row again.

    A plain UPDATE, not an upsert: a manufacturer that was ever actually
    released already has an `eudamed_sweep_state` row (the scheduler's tick
    writes `due_at` before a release is possible; `web/registry.py`'s release
    route only stamps `released_at` on top of that same row). The
    `no_manufacturer` exit calls this with an empty string, which matches no
    row and is a harmless no-op -- there is no manufacturer to identify a row
    for, so nothing needs clearing there in practice, but the exit is treated
    the same way rather than as a special case.
    """
    conn.execute(
        "UPDATE eudamed_sweep_state SET released_at = NULL, released_by = NULL "
        "WHERE canonical_name = %s", (manufacturer,))


def handle_eudamed_sweep(conn, job, *, fetcher=None) -> dict:
    """Mirror one canonical manufacturer's registered device catalogue.

    Released by a human, never self-emitted: Denis, 2026-08-20, restated
    2026-08-26 -- "no sweep ever runs unattended".
    """
    payload = job.get("payload") or {}
    manufacturer = (payload.get("manufacturer") or "").strip()
    r = Result()
    if not manufacturer:
        r.count("no_manufacturer")
        r.note("no manufacturer on the payload -- nothing to sweep")
        _clear_release(conn, manufacturer)
        return r.as_dict()

    srns = [row["srn"] for row in conn.execute(
        "SELECT srn FROM trusted_manufacturer_srn WHERE canonical_name = %s "
        "ORDER BY srn", (manufacturer,)).fetchall()]

    if not srns:
        # Task 4's certificate pull is the primary route to an SRN, but GC
        # EUROPE holds 356 device articles and zero certificates (measured
        # 2026-08-26) -- for a manufacturer like that, this article-probe
        # fallback is the only route left. Tried once per manufacturer, ever:
        # `srn_probed_at` is the stamp that stops a permanently-unregistered
        # supplier from being re-probed on every sweep.
        probed = conn.execute(
            "SELECT srn_probed_at FROM manufacturer WHERE canonical_name = %s",
            (manufacturer,)).fetchone()
        if probed is None or probed["srn_probed_at"] is None:
            # Lazy, and constructed only on this branch: a manufacturer that
            # is a permanent dead end (already probed, still no SRN) must
            # never allocate a fetcher it will not use. Tests inject a
            # FakeFetcher through this same parameter.
            if fetcher is None:
                fetcher = make_fetcher()
            found = probe_srn(conn, manufacturer, fetcher=fetcher)
            if found:
                r.count("probe_bootstrapped_srn")
                r.note(f"{manufacturer}: article probe found {found}")
                srns = [row["srn"] for row in conn.execute(
                    "SELECT srn FROM trusted_manufacturer_srn "
                    "WHERE canonical_name = %s ORDER BY srn",
                    (manufacturer,)).fetchall()]
            else:
                r.count("probe_found_nothing")
                r.note(f"{manufacturer}: article probe found no SRN")

    if not srns:
        r.count("no_trusted_srn")
        r.note(f"{manufacturer}: no auto or confirmed SRN -- nothing swept")
        _clear_release(conn, manufacturer)
        return r.as_dict()

    # Lazy here too: a manufacturer with a trusted SRN from the start (or one
    # the probe just bootstrapped, in which case this is a no-op -- `fetcher`
    # is already set) never went through the probe branch above.
    if fetcher is None:
        fetcher = make_fetcher()

    for srn in srns:
        for page in range(SWEEP_MAX_PAGES + 1):
            if page == SWEEP_MAX_PAGES:
                raise RuntimeError(
                    f"EUDAMED never reported a last page for {srn} -- "
                    f"stopped paging after {SWEEP_MAX_PAGES} pages"
                )
            if page:
                _pause_between_pages()
            resp = fetcher.get(EUDAMED_SRN_URL.format(
                srn=quote(srn, safe=""), size=SWEEP_PAGE_SIZE, page=page))
            if resp.status != 200:
                raise RuntimeError(f"EUDAMED returned {resp.status} for {srn}")
            try:
                body = json.loads(resp.body or b"")
            except (ValueError, TypeError) as exc:
                raise RuntimeError(
                    f"EUDAMED returned a non-JSON body for {srn}") from exc

            got = _devices(body)
            not_echoed = _assert_srn(got, srn)
            if not_echoed:
                r.count("srn_not_echoed", not_echoed)
            for d in got:
                udi_di = (d.get("primaryDi") or "").strip()
                if not udi_di:
                    r.count("skipped_no_udi_di")
                    continue
                reference = (d.get("reference") or "").strip() or None
                if reference is None:
                    r.count("missing_reference")
                conn.execute(_MIRROR_SWEEP_UPSERT, (
                    udi_di,
                    (d.get("basicUdi") or "").strip() or None,
                    (d.get("deviceName") or "").strip() or None,
                    (d.get("tradeName") or "").strip() or None,
                    srn,
                    reference,
                    _code(d.get("deviceStatusType")),
                ))
                r.count("devices")
            if _is_last(body, got):
                break

    # A sweep that stored nothing must NOT read as swept.
    #
    # The live failure of 2026-09-02: job 35679 swept IVOCLAR, wrote zero rows
    # (the 544 in the mirror still carried `synced_at` from 2026-08-25), and
    # still finished `done` with `last_swept_at = now()`. Page 0 came back with
    # no content, `_is_last` broke the loop before the body ran, and this stamp
    # fired anyway. The stamp is what makes it dangerous rather than merely
    # useless: it clears `due_at`, so the manufacturer drops off the operator's
    # due list for `eudamed_sweep_interval_days` -- 90 days of silence bought by
    # a sweep that did nothing.
    #
    # Zero devices for a TRUSTED srn is not a real outcome. The srn is trusted
    # because it came off a notified-body certificate or a double-gated article
    # probe, so the manufacturer is registered by construction. Zero means the
    # call failed in a way the 200 did not show.
    #
    # `released_at` is cleared either way: the release is a one-shot, and
    # leaving it set would make the row look queued forever and the due-list
    # tick would skip it (RULING 43).
    stored = r.as_dict()["counts"].get("devices", 0)
    if not stored:
        r.count("empty_sweep")
        r.note(
            f"{manufacturer}: stored no devices -- not stamping last_swept_at, "
            "leaving it due. A trusted SRN that returns nothing is a failed "
            "call, not an empty catalogue."
        )
        _clear_release(conn, manufacturer)
        return r.as_dict()

    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, last_swept_at, "
        "  due_at, released_at, released_by) "
        "VALUES (%s, now(), NULL, NULL, NULL) "
        "ON CONFLICT (canonical_name) DO UPDATE SET "
        "  last_swept_at = now(), due_at = NULL, "
        "  released_at = NULL, released_by = NULL",
        (manufacturer,),
    )
    return r.as_dict()


register("eudamed.sweep", handle_eudamed_sweep)
