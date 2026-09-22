"""The guided onboarding flow: a queue somebody can work through (S2.2).

Every control this uses already existed and was already tested. What did not
exist was a route between them. Onboarding a supplier meant: find the entity on
`/manufacturers?no_playbook=1`, press *Start a playbook* on its page, navigate
to `/playbooks/{slug}`, fill *Official domains* (card 3 of 13), scroll to
*Crawl recipes* (card 12, at character 65.421 of a 73.014-character page),
probe, save. Four controls on two pages, in no stated order, with no indication
that they belong to one job.

This module states the order and nothing else: it composes
`registry.start_playbook`, `registry.save_playbook_body` and the existing probe
routes, writing through the same optimistic lock and the same revision history.
The playbook page stays exactly as it is and remains the expert surface, so
anything done here can be inspected, edited or reverted there.

**The queue is the product.** 346 of 384 suppliers have no recipe; that is a
shift, not a form to fill in, and the count going down is what makes it one a
person can pick up and put down. Ordering is by item count because that is
where a recipe buys the most coverage.

**Three answers that are not "here is the recipe."** Measured 2026-09-04, the
top of the queue by item count is SANOLABOR (94 items) -- a DISTRIBUTOR, with
no compliance paperwork of its own to find. Without somewhere to record that,
the queue offers the same dead end every morning and is unusable by its second
row. `onboarding_state` (migration 060) is that somewhere: `not-a-manufacturer`,
`no-website`, `no-library`, each with a mandatory reason and a name, and each
clearable when the answer changes.

Producer-only where the pipeline is concerned (invariant 1): this writes
`manufacturer` (through the existing playbook save path) and `onboarding_state`,
and it never touches `document` / `item_document` / `evidence`.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from web.registry import (
    SaveRefused,
    save_playbook_body,
    start_playbook,
)

#: How many rows the queue shows. The queue is 346 long and nobody works it by
#: scrolling -- the top of the list IS the interface, and a longer page would
#: only make the count harder to see.
QUEUE_LIMIT = 12

#: The answers `onboarding_state` accepts, and what each says on screen. The
#: CHECK constraint in migration 060 holds the same set: a value absent from
#: either is refused, rather than stored and silently never shown.
EXITS = {
    "not-a-manufacturer": "issues no declarations of its own",
    "no-website": "has no site we can search",
    "no-library": "has a site but no page listing documents",
}


def queue_rows(conn, limit: int = QUEUE_LIMIT) -> list[dict]:
    """Suppliers with no search recipe, biggest gap first.

    `item_mirror` is joined through `manufacturer_alias` rather than through
    `item_group`: a supplier nobody has grouped yet has no groups at all, and
    those are exactly the ones this queue is for -- ordering by group count
    would put every never-touched entity at the bottom.

    Triaged-out entities drop out entirely (`cleared_at IS NULL`), which is the
    whole point of the table: the queue must not offer the same dead end twice.
    """
    return list(conn.execute(
        """
        SELECT m.canonical_name,
               count(DISTINCT im.item_ref) AS items,
               count(DISTINCT bc.code)     AS codes
        FROM manufacturer m
        JOIN manufacturer_alias a ON a.canonical_name = m.canonical_name
        JOIN item_mirror im       ON im.manufacturer_raw = a.raw_name
        LEFT JOIN manufacturer_bc_code bc ON bc.manufacturer_id = m.id
        WHERE m.slug IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM onboarding_state o
               WHERE o.canonical_name = m.canonical_name
                 AND o.cleared_at IS NULL)
        GROUP BY m.canonical_name
        ORDER BY count(DISTINCT im.item_ref) DESC, m.canonical_name
        LIMIT %s
        """,
        (limit,),
    ).fetchall())


def queue_size(conn) -> int:
    """The number in the corner. Counts the same population the list is drawn
    from, so the two can never disagree -- a count that outran its own list is
    how a worklist stops being believed."""
    return conn.execute(
        """
        SELECT count(*) AS n FROM (
          SELECT m.canonical_name
          FROM manufacturer m
          JOIN manufacturer_alias a ON a.canonical_name = m.canonical_name
          JOIN item_mirror im       ON im.manufacturer_raw = a.raw_name
          WHERE m.slug IS NULL
            AND NOT EXISTS (
                SELECT 1 FROM onboarding_state o
                 WHERE o.canonical_name = m.canonical_name
                   AND o.cleared_at IS NULL)
          GROUP BY m.canonical_name) s
        """
    ).fetchone()["n"]


def triaged_rows(conn) -> list[dict]:
    """The entities somebody took out of the queue, and why.

    Shown, never hidden: a queue that silently shrinks is one nobody can audit,
    and `not-a-manufacturer` on the wrong entity costs us every document it
    publishes.
    """
    return list(conn.execute(
        "SELECT canonical_name, state, reason, decided_by, at "
        "FROM onboarding_state WHERE cleared_at IS NULL "
        "ORDER BY at DESC"
    ).fetchall())


def suggest_slug(canonical_name: str) -> str:
    """A first guess at the page address, for the operator to accept or edit.

    Deliberately dumb and deliberately not enforced -- `start_playbook` owns
    the real rule and refuses anything else. Shorter than the legal name is
    normal (`ivoclar`, not `ivoclar-vivadent-ag`), which no derivation can
    decide, so this only lowercases and hyphenates and leaves the judgement to
    the person.
    """
    out = []
    for ch in canonical_name.casefold():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")


def entity_facts(conn, canonical_name: str) -> dict | None:
    """What the first screen states about a supplier before asking anything.

    A person deciding whether this is even a manufacturer needs the item count
    and the BC codes in front of them; asking for a slug first and the context
    never is how the SANOLABOR row gets a playbook nobody wanted.
    """
    row = conn.execute(
        "SELECT id, canonical_name, slug FROM manufacturer WHERE canonical_name=%s",
        (canonical_name,)).fetchone()
    if row is None:
        return None
    items = conn.execute(
        "SELECT count(DISTINCT im.item_ref) AS n "
        "FROM manufacturer_alias a JOIN item_mirror im ON im.manufacturer_raw = a.raw_name "
        "WHERE a.canonical_name = %s", (canonical_name,)).fetchone()["n"]
    codes = [r["code"] for r in conn.execute(
        "SELECT code FROM manufacturer_bc_code WHERE manufacturer_id=%s ORDER BY code",
        (row["id"],)).fetchall()]
    docs = conn.execute(
        "SELECT count(*) AS n FROM document "
        "WHERE canonical_manufacturer=%s AND status='production'",
        (canonical_name,)).fetchone()["n"]
    return {"canonical_name": canonical_name, "slug": row["slug"],
            "items": items, "codes": codes, "production_docs": docs,
            "suggested_slug": suggest_slug(canonical_name)}


def register_routes(app, templates, conn_factory, playbooks_dir: str | None,
                    authenticated_user=None, default_user: str = "user:admin",
                    require_operator=None) -> None:
    """`authenticated_user` is passed in rather than re-derived, the same as
    `registry.register_routes`: one place decides who the proxy says you are.

    `require_operator` (`web/access.py::operator_guard`) has to be declared
    here too, and that is not belt-and-braces. This module composes
    `start_playbook` and `save_playbook_body` by IMPORTING them, not by posting
    to `/playbooks/{slug}` -- which is the whole point of the flow, and means
    the guard on that route does nothing at all for these four. Every POST here
    writes: `start` creates the playbook, `domains` saves a body, and
    `skip`/`unskip` write `onboarding_state`. The four steps' PAGES stay
    reachable like every other operator page (D3).

    None means no route-level guard, for callers that build these routes
    directly in a test."""

    def _operator_only():
        return [Depends(require_operator)] if require_operator else []

    def _who(request: Request) -> str:
        return (authenticated_user(request) if authenticated_user else None) or default_user

    def _step(request, template: str, ctx: dict):
        with conn_factory() as conn:
            ctx.setdefault("queue", queue_rows(conn))
            ctx.setdefault("queue_size", queue_size(conn))
        ctx["request"] = request
        return templates.TemplateResponse(request, template, ctx)

    # --- the queue ---------------------------------------------------------

    @app.get("/onboarding", response_class=HTMLResponse)
    def onboarding_queue(request: Request, done: str = ""):
        with conn_factory() as conn:
            rows = queue_rows(conn)
            ctx = {
                "queue": rows,
                "queue_size": queue_size(conn),
                "triaged": triaged_rows(conn),
                # The exit reasons as the form offered them (spec § 9): the
                # queue lists what somebody chose, and `not-a-manufacturer` is
                # the stored slug, not the sentence they picked.
                "exits": EXITS,
                "done": done,
                # The first row is the one the flow opens on. Named rather than
                # implied so the button can say WHO, not "next".
                "next_up": rows[0] if rows else None,
            }
        return _step(request, "onboarding.html", ctx)

    # --- step 1: who -------------------------------------------------------

    @app.get("/onboarding/{canonical_name:path}/start", response_class=HTMLResponse)
    def onboarding_start(request: Request, canonical_name: str):
        with conn_factory() as conn:
            facts = entity_facts(conn, canonical_name)
        if facts is None:
            raise HTTPException(status_code=404, detail="unknown manufacturer")
        if facts["slug"]:
            # Already onboarded, by this flow or by hand. Send them to the
            # thing that exists rather than offering to create it twice.
            return RedirectResponse(f"/playbooks/{facts['slug']}", status_code=303)
        return _step(request, "onboarding_who.html",
                     {"facts": facts, "exits": EXITS})

    @app.post("/onboarding/{canonical_name:path}/start", response_class=HTMLResponse,
              dependencies=_operator_only())
    def onboarding_start_apply(request: Request, canonical_name: str,
                               slug: str = Form(...)):
        with conn_factory() as conn:
            try:
                out = start_playbook(conn, canonical_name=canonical_name,
                                     slug=slug, user=_who(request))
                conn.commit()
            except SaveRefused as exc:
                conn.rollback()
                facts = entity_facts(conn, canonical_name)
                return _step(request, "onboarding_who.html",
                             {"facts": facts, "exits": EXITS, "error": str(exc)})
        return RedirectResponse(f"/onboarding/{quote(out['slug'])}/domains",
                                status_code=303)

    # --- step 2: their websites -------------------------------------------

    @app.get("/onboarding/{slug}/domains", response_class=HTMLResponse)
    def onboarding_domains(request: Request, slug: str, error: str = ""):
        with conn_factory() as conn:
            row = conn.execute(
                "SELECT canonical_name, body, playbook_rev FROM manufacturer "
                "WHERE slug=%s", (slug,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="unknown playbook")
        return _step(request, "onboarding_domains.html", {
            "slug": slug, "canonical_name": row["canonical_name"],
            "rev": row["playbook_rev"],
            "domains": (row["body"] or {}).get("domains", []),
            "error": error,
        })

    @app.post("/onboarding/{slug}/domains", response_class=HTMLResponse,
              dependencies=_operator_only())
    def onboarding_domains_apply(request: Request, slug: str,
                                 domains: str = Form(""), rev: int = Form(...)):
        """Writes through `save_playbook_body` unchanged -- optimistic lock,
        revision recorded, `validate()` over the proposed set. This flow gets
        no write path of its own, so a domain saved here is a domain the
        playbook page can revert."""
        with conn_factory() as conn:
            row = conn.execute(
                "SELECT body FROM manufacturer WHERE slug=%s", (slug,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="unknown playbook")
            body = dict(row["body"] or {})
            entries = [d.strip() for d in domains.splitlines() if d.strip()]
            if entries:
                body["domains"] = entries
            else:
                body.pop("domains", None)
            try:
                save_playbook_body(
                    conn, slug=slug, body=body,
                    note=f"onboarding: {len(entries)} domain(s)",
                    user=_who(request), expected_rev=rev,
                    playbooks_dir=playbooks_dir)
                conn.commit()
            except SaveRefused as exc:
                conn.rollback()
                return RedirectResponse(
                    f"/onboarding/{quote(slug)}/domains?error={quote(str(exc))}",
                    status_code=303)
        return RedirectResponse(f"/onboarding/{quote(slug)}/library", status_code=303)

    # --- step 3: their library --------------------------------------------

    @app.get("/onboarding/{slug}/library", response_class=HTMLResponse)
    def onboarding_library(request: Request, slug: str):
        """The probe, in its own screen instead of twelfth on a long page.

        The form posts to the EXISTING `/playbooks/{slug}/probe` route and the
        result renders through the existing `_probe.html`: one probe
        implementation, so the guided flow and the expert page can never show
        an operator different answers for the same recipe.
        """
        with conn_factory() as conn:
            row = conn.execute(
                "SELECT canonical_name, body, playbook_rev FROM manufacturer "
                "WHERE slug=%s", (slug,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="unknown playbook")
        body = row["body"] or {}
        return _step(request, "onboarding_library.html", {
            "slug": slug, "canonical_name": row["canonical_name"],
            "rev": row["playbook_rev"],
            "domains": body.get("domains", []),
            "crawl": list(body.get("crawl", [])),
        })

    # --- step 4: done ------------------------------------------------------

    @app.get("/onboarding/{slug}/done", response_class=HTMLResponse)
    def onboarding_done(request: Request, slug: str):
        with conn_factory() as conn:
            row = conn.execute(
                "SELECT canonical_name, body, playbook_rev FROM manufacturer "
                "WHERE slug=%s", (slug,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="unknown playbook")
            items = conn.execute(
                "SELECT count(DISTINCT im.item_ref) AS n FROM manufacturer_alias a "
                "JOIN item_mirror im ON im.manufacturer_raw = a.raw_name "
                "WHERE a.canonical_name = %s",
                (row["canonical_name"],)).fetchone()["n"]
        body = row["body"] or {}
        return _step(request, "onboarding_done.html", {
            "slug": slug, "canonical_name": row["canonical_name"],
            "rev": row["playbook_rev"], "items": items,
            "domains": body.get("domains", []),
            "crawl": list(body.get("crawl", [])),
        })

    # --- the exits ---------------------------------------------------------

    @app.post("/onboarding/{canonical_name:path}/skip", response_class=HTMLResponse,
              dependencies=_operator_only())
    def onboarding_skip(request: Request, canonical_name: str,
                        state: str = Form(...), reason: str = Form("")):
        """Take an entity out of the queue, with a reason and a name.

        The reason is required and the route refuses a blank one, the same
        discipline `skip_backfill` applies at parse time: months later, "this
        supplier files nothing" and "we agreed it has nothing to file" look
        identical from the outside.
        """
        ctx = {"request": request}
        if state not in EXITS:
            ctx["error"] = f"unknown reason {state!r}"
            return templates.TemplateResponse(request, "_result.html", ctx,
                                              status_code=422)
        if not reason.strip():
            ctx["error"] = ("say why in a few words — a skip nobody can account "
                            "for later reads as a supplier that files nothing")
            return templates.TemplateResponse(request, "_result.html", ctx,
                                              status_code=422)
        with conn_factory() as conn:
            known = conn.execute(
                "SELECT 1 FROM manufacturer WHERE canonical_name=%s",
                (canonical_name,)).fetchone()
            if known is None:
                raise HTTPException(status_code=404, detail="unknown manufacturer")
            # The partial unique index allows exactly one ACTIVE note; a repeat
            # press updates the reason rather than failing at the constraint.
            conn.execute(
                "INSERT INTO onboarding_state (canonical_name, state, reason, decided_by) "
                "VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (canonical_name) WHERE cleared_at IS NULL "
                "DO UPDATE SET state=EXCLUDED.state, reason=EXCLUDED.reason, "
                "  decided_by=EXCLUDED.decided_by, at=now()",
                (canonical_name, state, reason.strip(), _who(request)))
            conn.commit()
        return RedirectResponse(
            f"/onboarding?done=skipped:{quote(canonical_name)}", status_code=303)

    @app.post("/onboarding/{canonical_name:path}/unskip", response_class=HTMLResponse,
              dependencies=_operator_only())
    def onboarding_unskip(request: Request, canonical_name: str):
        """Put an entity back in the queue. The note is KEPT and cleared, never
        deleted: who decided what, and who undid it, are both part of the
        trail, and 060 grants no DELETE."""
        with conn_factory() as conn:
            hit = conn.execute(
                "UPDATE onboarding_state SET cleared_at=now(), cleared_by=%s "
                "WHERE canonical_name=%s AND cleared_at IS NULL RETURNING id",
                (_who(request), canonical_name)).fetchone()
            conn.commit()
        if hit is None:
            raise HTTPException(status_code=404,
                                detail="that entity is not out of the queue")
        return RedirectResponse(
            f"/onboarding?done=returned:{quote(canonical_name)}", status_code=303)
