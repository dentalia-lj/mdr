"""Named logins and the operator refusal (office UI redesign spec § 7 P5b, D2, D3).

D3 splits the operator surface in two, deliberately unevenly. Every operator
PAGE stays reachable by URL -- hiding a page from the menu is a kindness to an
office reader, not a security boundary, and pretending otherwise would invite
someone to rely on it. What is actually refused is a short list of WRITES: the
playbook edit and the code claim, the Business Central push apply, the two
scheduler buttons, and `/ingest`. Those four are the ones that change how the
machine runs or spend money, and none of them is office work.

`/import` apply is the deliberate exception and has its own test here. It is
the office's own job -- it is how the catalogue arrives -- and an office login
that could not press it would be an office login that cannot work.

The state everywhere today is `web.operator_users` EMPTY, which makes every
login an operator and refuses nothing. That is not a placeholder to be tidied
away later: it is what stops this guard locking the office out of a running
system before anyone has configured a single name. Both states are tested.
"""

from __future__ import annotations

import logging
import pathlib

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Json

from app.config import Web
from tests.conftest import TEST_API_URL as API_TEST_URL
from web.app import create_app

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _client(tmp_path, operator_users=()):
    """Behind the proxy, the way compose runs it: the login arrives in
    `X-Forwarded-User` (the same shape tests/test_web_failed_split.py uses)."""
    cfg = Web(api_database_url=API_TEST_URL, imports_dir=str(tmp_path),
              require_authenticated_user=True, operator_users=operator_users)
    return TestClient(create_app(cfg))


def _as(user: str) -> dict[str, str]:
    return {"X-Forwarded-User": user}


#: The four D3 writes, as (path, form) pairs. Two of them are one route each
#: and two are two, which is why "four" is a count of decisions and six is a
#: count of routes.
#:
#: The slugs and ids are deliberately ones that do not exist. The guard is a
#: route dependency, so it answers before the handler looks anything up: a 403
#: here proves the refusal, and anything that is NOT a 403 proves the request
#: reached the handler, which is the whole question these tests ask.
GUARDED = [
    ("/playbooks/no-such-playbook", {"rev": "0"}),
    ("/playbooks/no-such-playbook/codes", {"code": "ACME"}),
    ("/bc-push/apply", {}),
    ("/scheduler/run/report.weekly", {}),
    ("/scheduler/arm/report.weekly", {}),
    ("/ingest", {"source": "csv", "priority": "interactive",
                 "csv_path_manual": "/imports/nothing.xlsx"}),
]
GUARDED_IDS = [path for path, _ in GUARDED]


def _seed_preview(conn):
    """A finished import preview, as the worker would have left it -- the row
    `/import/{id}/apply` needs to have something real to apply."""
    upload_id = conn.execute(
        "INSERT INTO import_inbox (kind, filename, content, catalogue) "
        "VALUES ('items','Artikli.csv','\\x00','LJ') RETURNING id").fetchone()["id"]
    payload = {"source": "upload", "ref": {"upload_id": upload_id},
               "catalogue": "LJ", "dry_run": True, "filename": "Artikli.csv"}
    jid = conn.execute(
        "INSERT INTO job (type, payload, dedupe_key, status, result) "
        "VALUES ('ingest.run', %s, %s, 'done', %s) RETURNING id",
        (Json(payload), f"ingest.run:upload:{upload_id}:preview",
         Json({"seen": 2, "changed": 1, "unchanged": 1, "dry_run": True})),
    ).fetchone()["id"]
    conn.commit()
    return jid, upload_id


# --------------------------------------------------------------------------- #
# 1. A non-operator is refused, and told so on a page a person can read.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("path,form", GUARDED, ids=GUARDED_IDS)
def test_a_non_operator_is_refused_each_of_the_d3_writes(tmp_path, conn, path, form):
    client = _client(tmp_path, operator_users=("denis",))
    before = conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"]

    resp = client.post(path, data=form, headers=_as("office1"))

    assert resp.status_code == 403, resp.text
    # Nothing half-done: the refusal happens before the handler, so no job.
    after = conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"]
    assert after == before


@pytest.mark.parametrize("path,form", GUARDED, ids=GUARDED_IDS)
def test_the_refusal_is_the_html_error_page_not_a_json_blob(tmp_path, conn, path, form):
    """P7a's page, reached through `_error_response` -- there is no second
    error path for this refusal, and building one would give an office reader
    two different-looking 403s for the same word."""
    client = _client(tmp_path, operator_users=("denis",))
    resp = client.post(path, data=form, headers=_as("office1"))

    assert resp.headers["content-type"].startswith("text/html")
    assert "You cannot open this" in resp.text
    assert "Go to Today" in resp.text
    # The sentence that says WHICH refusal this is, under Technical details.
    assert "This action is for operators." in resp.text


def test_a_json_caller_still_gets_json(tmp_path, conn):
    """Same handler, same decision as every other error: a caller that asked
    for JSON is not handed a menu."""
    client = _client(tmp_path, operator_users=("denis",))
    resp = client.post("/scheduler/arm/report.weekly",
                       headers={**_as("office1"), "Accept": "application/json"})

    assert resp.status_code == 403
    assert resp.json() == {"detail": "This action is for operators."}


# --------------------------------------------------------------------------- #
# 2. An operator is not refused, and gets exactly what they got before.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("path,form", GUARDED, ids=GUARDED_IDS)
def test_an_operator_reaches_every_one_of_them(tmp_path, conn, path, form):
    client = _client(tmp_path, operator_users=("denis",))
    resp = client.post(path, data=form, headers=_as("denis"))
    assert resp.status_code != 403, resp.text


@pytest.mark.parametrize("path,form", GUARDED, ids=GUARDED_IDS)
def test_an_operator_gets_the_answer_an_unguarded_login_used_to_get(
        tmp_path, conn, path, form):
    """"Behaves as before" stated as a comparison rather than as a guess: the
    same request, once with the list configured and once with it empty (which
    is how every deployment stands today), must answer identically."""
    guarded = _client(tmp_path, operator_users=("denis",))
    unguarded = _client(tmp_path, operator_users=())

    after_guard = guarded.post(path, data=form, headers=_as("denis"))
    conn.execute("DELETE FROM job")
    conn.commit()
    before_guard = unguarded.post(path, data=form, headers=_as("denis"))

    assert after_guard.status_code == before_guard.status_code


# --------------------------------------------------------------------------- #
# 3. `/import` apply stays the office's own.
# --------------------------------------------------------------------------- #

def test_import_apply_stays_open_to_an_office_login(tmp_path, conn):
    """D3's named exception. This is how the catalogue arrives; an office login
    that cannot press Apply is an office login that cannot do its job."""
    jid, upload_id = _seed_preview(conn)
    client = _client(tmp_path, operator_users=("denis",))

    resp = client.post(f"/import/{jid}/apply", headers=_as("office1"))

    assert resp.status_code == 200, resp.text
    queued = conn.execute(
        "SELECT id FROM job WHERE dedupe_key=%s",
        (f"ingest.run:upload:{upload_id}:apply",)).fetchone()
    assert queued is not None


def test_the_import_page_and_form_stay_open_to_an_office_login(tmp_path, conn):
    client = _client(tmp_path, operator_users=("denis",))
    assert client.get("/import", headers=_as("office1")).status_code == 200


# --------------------------------------------------------------------------- #
# 4. The menu.
# --------------------------------------------------------------------------- #

def test_the_menu_hides_the_operator_group_from_an_office_login(tmp_path, conn):
    client = _client(tmp_path, operator_users=("denis",))
    resp = client.get("/", headers=_as("office1"))
    assert resp.status_code == 200
    assert 'id="nav-operator"' not in resp.text


def test_the_menu_shows_the_operator_group_to_an_operator(tmp_path, conn):
    client = _client(tmp_path, operator_users=("denis",))
    resp = client.get("/", headers=_as("denis"))
    assert resp.status_code == 200
    assert 'id="nav-operator"' in resp.text


def test_an_operator_page_is_still_reachable_by_url_for_the_office(tmp_path, conn):
    """D3 as written: hidden from the menu, not refused. Hiding a page is
    tidiness; only the four writes are a boundary."""
    client = _client(tmp_path, operator_users=("denis",))
    assert client.get("/scheduler", headers=_as("office1")).status_code == 200
    assert client.get("/playbooks", headers=_as("office1")).status_code == 200


# --------------------------------------------------------------------------- #
# 5. The empty list, which is every deployment today.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("path,form", GUARDED, ids=GUARDED_IDS)
def test_an_empty_list_makes_every_login_an_operator(tmp_path, conn, path, form):
    """The guard must not be able to lock the office out of a running system
    before anyone has configured a name. Empty means everyone."""
    client = _client(tmp_path, operator_users=())
    resp = client.post(path, data=form, headers=_as("anybody"))
    assert resp.status_code != 403, resp.text


def test_an_empty_list_still_shows_everyone_the_operator_group(tmp_path, conn):
    client = _client(tmp_path, operator_users=())
    resp = client.get("/", headers=_as("anybody"))
    assert 'id="nav-operator"' in resp.text


# --------------------------------------------------------------------------- #
# 6. The deployment side. A guard nobody can switch on is not a guard.
# --------------------------------------------------------------------------- #

def test_compose_passes_the_operator_list_into_the_web_container():
    """Compose reads `.env` for ${} interpolation only and injects nothing, so
    a key absent from `web`'s environment: block is unsettable, silently --
    which is exactly what it was until this slice."""
    yaml = pytest.importorskip("yaml")
    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text())
    env = compose["services"]["web"].get("environment") or {}
    assert "WEB_OPERATOR_USERS" in env


def test_compose_mounts_the_caddy_users_directory():
    yaml = pytest.importorskip("yaml")
    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text())
    mounts = compose["services"]["caddy"].get("volumes") or []
    assert any("/etc/caddy/users" in str(m) for m in mounts), mounts


def test_the_caddyfile_reads_the_users_file_inside_its_one_basic_auth_block():
    """Verified with `caddy validate` against the project's own caddy:2.8 image
    on 2026-09-15: `import` is spliced in at parse time, so the imported lines
    ARE accounts of this block (a username with no hash fails the parse, naming
    the imported file). A glob that matches nothing is a warning, not an error,
    so an empty `caddy/users/` still starts the proxy on the env pair alone."""
    text = (_ROOT / "Caddyfile").read_text()
    assert text.count("basic_auth {") == 1
    # Split on the block's own closing line, not the first "}" -- the first
    # one in there closes `{$DENTALIA_WEB_USER}`.
    block = text.split("basic_auth {", 1)[1].split("\n\t\t}", 1)[0]
    assert "{$DENTALIA_WEB_USER} {$DENTALIA_WEB_PASSWORD_HASH}" in block
    assert "import /etc/caddy/users/*.caddy" in block


def test_no_real_login_file_is_committed():
    """The account files carry bcrypt hashes. The directory is committed so the
    bind-mount has somewhere to land; its contents are not."""
    assert (_ROOT / "caddy" / "users" / ".gitignore").exists()
    assert list((_ROOT / "caddy" / "users").glob("*.caddy")) == []


# =========================================================================== #
# Fix round 1 (controller, 2026-09-15).
# =========================================================================== #

# --------------------------------------------------------------------------- #
# 7. D6's other half: the per-group re-run on /dead.
#
# The Failed split exists so the office can clear its own failures, so
# `/dead/retry-timeouts` and `/dead/search-again` are office actions and stay
# open to everyone -- Today posts to both. What only an operator gets is the
# developer section's per-cause re-run, which re-queues work whose cause is
# still unfixed.
# --------------------------------------------------------------------------- #

def _dead_job(conn, *, job_type="extract.doc", error="ValueError: boom",
              key="dead:one", payload=None) -> int:
    """One dead job. The default lands in the DEVELOPER section: a ValueError on
    a type that never contacts a supplier website is nobody's timeout and
    nobody's dead address."""
    jid = conn.execute(
        "INSERT INTO job (type, payload, dedupe_key, status, priority, attempts, "
        "                 max_attempts, last_error, finished_at) "
        "VALUES (%s::job_type, %s, %s, 'dead', 'sweep', 5, 5, %s, now()) RETURNING id",
        (job_type, Json(payload or {}), key, error),
    ).fetchone()["id"]
    conn.commit()
    return jid


OFFICE_FAILURE_ACTIONS = ["/dead/retry-timeouts", "/dead/search-again"]


def test_the_per_group_rerun_is_refused_to_an_office_login(tmp_path, conn):
    jid = _dead_job(conn)
    client = _client(tmp_path, operator_users=("denis",))

    group = client.post("/dead/rerun-group", data={"digest": "whatever"},
                        headers=_as("office1"))
    single = client.post(f"/dead/{jid}/rerun", headers=_as("office1"))

    assert group.status_code == 403, group.text
    assert single.status_code == 403, single.text
    assert "This action is for operators." in group.text
    # Refused before the handler, so nothing was re-queued.
    assert conn.execute(
        "SELECT count(*) AS n FROM job WHERE status <> 'dead'").fetchone()["n"] == 0


def test_an_operator_keeps_the_per_group_rerun(tmp_path, conn):
    jid = _dead_job(conn)
    client = _client(tmp_path, operator_users=("denis",))

    assert client.post("/dead/rerun-group", data={"digest": "whatever"},
                       headers=_as("denis")).status_code != 403
    assert client.post(f"/dead/{jid}/rerun",
                       headers=_as("denis")).status_code != 403


@pytest.mark.parametrize("path", OFFICE_FAILURE_ACTIONS)
def test_the_office_keeps_try_again_and_search_again(tmp_path, conn, path):
    """D6: office logins get "took too long" and the dead addresses. Today posts
    to both of these, and an office person who could not press them would be
    reading a page of work nobody can clear."""
    client = _client(tmp_path, operator_users=("denis",))
    assert client.post(path, headers=_as("office1")).status_code != 403


def _all_three_failure_classes(conn) -> int:
    """One dead job of each class, so the Failed page renders all three
    sections with their buttons. Each button is conditional on its own section
    having work, which is why a developer-fault row alone is not enough."""
    _dead_job(conn, job_type="fetch.url", error="ConnectTimeout: timed out",
              key="dead:timeout", payload={"url": "https://a.example/x",
                                           "domain": "a.example", "group_id": 11})
    _dead_job(conn, job_type="fetch.url", error="unexpected status 404",
              key="dead:gone", payload={"url": "https://b.example/y",
                                        "domain": "b.example", "group_id": 12})
    return _dead_job(conn)


def test_the_failed_page_offers_the_office_no_button_it_would_refuse(tmp_path, conn):
    """Spec § 4: the developer-fault section has no buttons for office logins.
    A button that always answers 403 is worse than no button."""
    _all_three_failure_classes(conn)
    client = _client(tmp_path, operator_users=("denis",))

    office = client.get("/dead", headers=_as("office1"))
    operator = client.get("/dead", headers=_as("denis"))

    assert office.status_code == 200
    assert "/dead/rerun-group" not in office.text
    assert "/rerun" not in office.text
    # And the office actions are still on the page for them.
    for path in OFFICE_FAILURE_ACTIONS:
        assert path in office.text

    assert "/dead/rerun-group" in operator.text
    assert "/rerun" in operator.text


def test_an_empty_list_keeps_the_per_group_rerun_for_everyone(tmp_path, conn):
    jid = _dead_job(conn)
    client = _client(tmp_path, operator_users=())

    # The page FIRST: a re-queued dead job stops counting in the split, so the
    # group it was the whole of would be gone by the time the POSTs are done.
    assert "/dead/rerun-group" in client.get("/dead", headers=_as("anybody")).text
    assert client.post("/dead/rerun-group", data={"digest": "whatever"},
                       headers=_as("anybody")).status_code != 403
    assert client.post(f"/dead/{jid}/rerun",
                       headers=_as("anybody")).status_code != 403


# --------------------------------------------------------------------------- #
# 8. The playbook writes that sit next to the two the first round guarded.
#
# `revert` writes a playbook body forward as a new revision, so it is a playbook
# edit by any reading of D3. `probe` and `crawl` are the money: the probe
# enqueues a job that fetches, and `crawl` saves what it found. `onboarding` is
# the same three writers reached through a guided flow -- and it reaches them by
# importing `start_playbook` / `save_playbook_body` DIRECTLY, not by posting to
# the guarded routes, so guarding `/playbooks/*` does nothing for it.
# --------------------------------------------------------------------------- #

GUARDED_ROUND_2 = [
    ("/playbooks/no-such-playbook/revert", {"to_rev": "1", "rev": "2"}),
    ("/playbooks/no-such-playbook/probe", {"rev": "1"}),
    ("/playbooks/no-such-playbook/crawl", {"rev": "1"}),
    ("/onboarding/No Such Maker/start", {}),
    ("/onboarding/no-such-playbook/domains", {"rev": "1"}),
    ("/onboarding/No Such Maker/skip", {"reason": "not-a-manufacturer"}),
    ("/onboarding/No Such Maker/unskip", {}),
]
ROUND_2_IDS = [path for path, _ in GUARDED_ROUND_2]


@pytest.mark.parametrize("path,form", GUARDED_ROUND_2, ids=ROUND_2_IDS)
def test_the_adjacent_playbook_writes_are_refused_to_an_office_login(
        tmp_path, conn, path, form):
    client = _client(tmp_path, operator_users=("denis",))
    before = conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"]

    resp = client.post(path, data=form, headers=_as("office1"))

    assert resp.status_code == 403, resp.text
    assert conn.execute(
        "SELECT count(*) AS n FROM job").fetchone()["n"] == before


@pytest.mark.parametrize("path,form", GUARDED_ROUND_2, ids=ROUND_2_IDS)
def test_an_operator_reaches_the_adjacent_playbook_writes(tmp_path, conn, path, form):
    client = _client(tmp_path, operator_users=("denis",))
    assert client.post(path, data=form,
                       headers=_as("denis")).status_code != 403


@pytest.mark.parametrize("path,form", GUARDED_ROUND_2, ids=ROUND_2_IDS)
def test_an_empty_list_reaches_the_adjacent_playbook_writes(tmp_path, conn, path, form):
    client = _client(tmp_path, operator_users=())
    assert client.post(path, data=form,
                       headers=_as("anybody")).status_code != 403


# --------------------------------------------------------------------------- #
# 9. The HTMX fragment says which refusal it is, for 403 only.
#
# Every button on this site posts over HTMX, so the fragment -- not the page --
# is how a refusal actually reaches a person. `_error_response` rendered the
# generic sentence for it, which for the operator refusal is the wrong one.
# Widened for 403 ONLY, because every 403 detail in this app is our own fixed
# string; a 500's detail is deliberately fixed too, but the rule stays narrow so
# that a future status carrying an exception message cannot ride in on it.
# --------------------------------------------------------------------------- #

def test_an_htmx_403_fragment_carries_the_operator_sentence(tmp_path, conn):
    client = _client(tmp_path, operator_users=("denis",))
    resp = client.post("/scheduler/run/report.weekly",
                       headers={**_as("office1"), "HX-Request": "true"})

    assert resp.status_code == 403
    assert 'class="result error"' in resp.text
    assert "This action is for operators." in resp.text
    assert 'class="sidebar"' not in resp.text


def test_an_htmx_500_fragment_still_says_only_the_generic_words(test_db_url, tmp_path):
    """The narrowing, pinned. A 500 must keep the generic sentence and must
    never carry the exception, which is the whole reason its detail is fixed."""
    app = create_app(Web(api_database_url=API_TEST_URL, imports_dir=str(tmp_path)))

    @app.get("/boom-operator-partial")
    def boom():
        raise RuntimeError("a connection string nobody should read")

    resp = TestClient(app, raise_server_exceptions=False).get(
        "/boom-operator-partial", headers={"HX-Request": "true"})

    assert resp.status_code == 500
    assert 'class="result error"' in resp.text
    assert "The system could not finish this." in resp.text
    assert "connection string nobody should read" not in resp.text
    assert "RuntimeError" not in resp.text
    assert "internal server error" not in resp.text


def test_no_refusal_anywhere_names_the_header_that_carries_identity():
    """Since the P5b fix round a 403 renders its `detail` in the page and in an
    HTMX fragment, so a detail naming `trusted_user_header` would print the name
    of the header that carries identity.

    In practice `access.guard` refuses an unproxied request first, with a
    generic sentence, so no such detail is reachable over HTTP today. This is
    belt and braces, and it is static rather than a route test on purpose:
    anyone who reaches such a refusal is talking to `web` directly, past Caddy,
    and in that position the header's name is the whole of what stands between
    them and sending it themselves to become an operator. One new `detail=`
    written months from now is all it would take, and no route test would cover
    the route that does not exist yet.

    The name belongs in the log, where whoever is debugging the proxy looks."""
    web = pathlib.Path(__file__).resolve().parents[1] / "web"

    bad = []
    for src in sorted(web.rglob("*.py")):
        for line_no, line in enumerate(src.read_text(encoding="utf-8").splitlines(), 1):
            if "detail=" in line and "trusted_user_header" in line:
                bad.append(f"{src.name}:{line_no} {line.strip()[:70]}")
    assert bad == [], (
        "a refusal names the header that carries identity; log it instead: "
        + "; ".join(bad))


def test_starting_a_playbook_from_the_manufacturer_page_is_operator_only(tmp_path):
    """The second door to `start_playbook`.

    P5b guarded `/onboarding/{name}/start` after finding that onboarding reached
    the writer by import rather than by posting to the playbook route. This
    route reaches the SAME writer by a third way, and its button rendered on
    every un-onboarded manufacturer's page. It was found by asking whether the
    guard could be walked around, not by reading the list of routes the slice
    had named: a guard on a writer is only worth the weakest route that reaches
    it.

    The name does not exist, deliberately, the way `GUARDED` above works: the
    guard is a route dependency, so a 403 proves the refusal and anything else
    proves the request reached the handler."""
    client = _client(tmp_path, operator_users=("denis",))

    refused = client.post("/manufacturers/NO-SUCH-MAKER/playbook",
                          data={"slug": "no-such"}, headers=_as("office1"))
    assert refused.status_code == 403, refused.text

    allowed = client.post("/manufacturers/NO-SUCH-MAKER/playbook",
                          data={"slug": "no-such"}, headers=_as("denis"))
    assert allowed.status_code != 403

    with_no_names = _client(tmp_path).post(
        "/manufacturers/NO-SUCH-MAKER/playbook", data={"slug": "no-such"},
        headers=_as("office1"))
    assert with_no_names.status_code != 403, (
        "with no names configured everyone is still an operator")


def test_the_manufacturer_page_offers_no_button_it_would_refuse():
    """Read from the template, not a rendered page: the form sits behind
    `{% if playbook is none %}`, so a fixture has to seed a manufacturer in
    exactly that state to see it at all, and the branch that matters is the one
    a test is most likely to miss (spec § 4)."""
    tpl = (pathlib.Path(__file__).resolve().parents[1]
           / "web" / "templates" / "manufacturer_detail.html").read_text()

    form = tpl.index('hx-post="/manufacturers/{{ canonical_name }}/playbook"')
    before = tpl[:form]
    guard = before.rindex("{% if is_operator(request) %}")
    assert guard > before.rindex("{% endif %}", 0, guard), (
        "the Start it form must sit inside the operator condition")
    assert "Starting a playbook is an operator action." in tpl
