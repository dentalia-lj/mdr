"""`bc.push` — write three compliance fields into Business Central.

The stage. The rule it applies is `app/bc_fields.py`, which has no connection
and no HTTP and is tested on its own; this module reads the registry, diffs
against what we last sent, and PATCHes only the difference.

Leaf stage: it emits nothing and writes no registry table. `bc_push_log`
(migration 063) is its only write, and doubles as the diff source so BC is
never read back to discover what we last told it.

Design: `docs/superpowers/specs/2026-09-07-bc-writeback-design.md`.
"""

from __future__ import annotations

from datetime import date

from app import bc_fields
from app.config import load_config
from app.handlers import register
from app.results import Result

#: Every holding the rule needs, for one item. `document_effective_expiry`
#: supplies the basis, which is what decides whether a passed date falsifies.
#:
#: The EUDAMED certificate status is read the way `certificate_drift` reads it
#: (migrations 043/058): only under a trusted SRN of the item's manufacturer,
#: against the base number with the `R2` / `Rev. 2` suffix stripped exactly as
#: `held_certificate` strips it, and only the latest revision. It used to be
#: joined on the printed number alone, so any company's certificate with a
#: colliding number could falsify `pteValidCECertificate` (doc 443 on the live
#: registry matched Guilin Woodpecker's), and two revisions returned two rows,
#: letting an older `issued` row outvote a newer withdrawal.
#:
#: **The body is shared, and that is the point.** `web/bc_push_view.py`'s bulk
#: preview asks this same question for a page of items, and until 2026-09-14 it
#: asked it with its own unscoped join — so the page an operator reads before
#: pressing Apply could disagree with the job that writes. Measured that day: 55
#: of the 123 production documents carrying a certificate number matched a
#: FOREIGN actor on the bare number, touching 640 production-linked items. Two
#: queries for one rule is how that happened; there is one now, and the WHERE
#: clause is all a caller adds.
_HOLDINGS_SELECT = r"""
SELECT l.item_ref,
       d.type          AS doc_type,
       d.status        AS doc_status,
       l.status        AS link_status,
       d.coverage_scope,
       e.expires,
       e.basis         AS expiry_basis,
       c.certificate_status
  FROM item_document l
  JOIN document d ON d.doc_id = l.doc_id
  LEFT JOIN document_effective_expiry e ON e.doc_id = d.doc_id
  LEFT JOIN LATERAL (
        SELECT ec.certificate_status
          FROM item_group_member gm
          JOIN item_group g ON g.group_id = gm.group_id
          JOIN trusted_manufacturer_srn s ON s.canonical_name = g.canonical_manufacturer
          JOIN eudamed_certificate ec
            ON ec.actor_srn = s.srn
           AND ec.certificate_number = btrim(regexp_replace(regexp_replace(
                   d.cert_number, '\s+R[0-9]+$', ''), '\s*Rev\.?\s*[0-9]+$', '', 'i'))
         WHERE gm.item_ref = l.item_ref
         ORDER BY NULLIF(regexp_replace(ec.revision_number, '\D', '', 'g'), '')::int
                  DESC NULLS LAST, ec.synced_at DESC
         LIMIT 1
  ) c ON TRUE
"""

#: One item, for the stage.
_HOLDINGS_SQL = _HOLDINGS_SELECT + " WHERE l.item_ref = %s"

#: A page of items, for the bulk preview. Same rule, one round trip.
HOLDINGS_FOR_MANY_SQL = _HOLDINGS_SELECT + " WHERE l.item_ref = ANY(%s)"


def _accepted(status) -> bool:
    """Did BC take it? Only a 2xx is a write."""
    return status is not None and 200 <= status < 300


def _last_sent(conn, item_ref: str) -> dict[str, str]:
    """What BC last ACCEPTED for this item, field by field.

    Refused attempts are kept in the ledger and excluded here on purpose. A
    rejected value that counted as sent would leave the next run's diff looking
    clean, so BC would keep a value it never took and nothing would ever
    correct it.
    """
    rows = conn.execute(
        "SELECT DISTINCT ON (field) field, new_value "
        "  FROM bc_push_log WHERE item_ref = %s "
        "   AND http_status BETWEEN 200 AND 299 "
        " ORDER BY field, pushed_at DESC, id DESC",
        (item_ref,),
    ).fetchall()
    return {r["field"]: r["new_value"] for r in rows}


def _wire(value) -> str:
    """One field as the ledger stores it: booleans lower-case, text verbatim."""
    return "true" if value is True else "false" if value is False else str(value)


def handle_bc_push(conn, job: dict, *, client=None) -> dict:
    cfg = load_config()
    today = date.today()
    r = Result()
    for key in ("seen", "sent", "unchanged", "unprocessed", "failed",
                "absent", "withheld", "would_send"):
        r.count(key, 0)

    for item_ref in job["payload"]["item_refs"]:
        r.count("seen")
        holdings = conn.execute(_HOLDINGS_SQL, (item_ref,)).fetchall()
        fields = bc_fields.fields_for(
            item_ref,
            holdings,
            processed=bool(holdings),
            today=today,
            base_url=cfg.web.public_base_url,
            link_key=cfg.web.bc_link_key,
        )
        if fields is None:
            r.count("unprocessed")
            continue

        last = _last_sent(conn, item_ref)
        changed = {k: v for k, v in fields.items() if last.get(k) != _wire(v)}
        if not changed:
            r.count("unchanged")
            continue

        if not cfg.bc.write_enabled:
            # Off is still useful: this count is what the bulk preview reads,
            # and a withheld push writes no ledger row because nothing was sent.
            r.count("withheld")
            r.count("would_send")
            r.sample("would_send", {"item_ref": item_ref,
                                    "fields": sorted(changed)})
            continue

        status, body = client.patch(item_ref, changed)
        for field, value in changed.items():
            conn.execute(
                "INSERT INTO bc_push_log (item_ref, field, old_value, new_value, "
                "http_status, response, via_job) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (item_ref, field, last.get(field), _wire(value), status, body,
                 job.get("id")),
            )
        if _accepted(status):
            r.count("sent")
        elif status == 404:
            # `dataitems` is "a subset" (b-s.si, 2026-09-02) and nobody has said
            # of what. If it is filtered, the items we mean to write are simply
            # not on the page -- and a 404 folded into failures would hide that
            # behind retries. Counted and named separately, the first real run
            # answers a question nobody asked. Not acceptance: the ledger row
            # carries the 404 and the diff still excludes it, so the value goes
            # again if the page is ever widened.
            r.count("absent")
            r.sample("absent", {"item_ref": item_ref, "fields": sorted(changed)})
        else:
            r.count("failed")
            r.sample("refused", {"item_ref": item_ref, "http_status": status,
                                 "fields": sorted(changed)})

    return r.as_dict()


register("bc.push", handle_bc_push)
