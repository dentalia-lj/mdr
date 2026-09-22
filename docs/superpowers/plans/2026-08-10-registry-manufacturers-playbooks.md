# Registry Views: Manufacturers + Playbooks — Implementation Plan

**Goal:** Add four read-only pages to the producer UI — a playbook-coverage triage list of the 386 manufacturer entities, an entity detail page, a playbook list, and a playbook detail page.

**Architecture:** A new `web/registry.py` owns the queries and routes; `web/app.py` gains two lines (an import and a `register_routes(...)` call inside `create_app`). Manufacturer aggregates come from one SQL query over `manufacturer_alias`/`item_mirror`/`item_document_production`; playbook facts come from `app.playbooks.load_playbooks()`, which reads JSON files. Filtering and sorting happen in Python — the entity set is bounded at ~390 rows, so a second SQL path and a shared LIKE-escaping helper both earn nothing.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, HTMX (no build step), psycopg 3 with dict rows, pytest against a real Postgres.

**Spec:** `docs/superpowers/specs/2026-08-10-registry-manufacturers-playbooks-design.md`

## Global Constraints

- **Read-only.** These routes are all `GET`. No form, no POST, no `enqueue`, no write to any table. The `dentalia_api` role holds SELECT everywhere except `job` and `upload_inbox`, so a write attempt fails at the database — do not try to work around it.
- **Never `import app.handlers` or `app.workers` from `web/`.** `app.playbooks`, `app.config` and `app.db` are permitted (`web/app.py` already imports the latter two).
- **COORDINATE BEFORE RUNNING THE TEST SUITE.** `tests/conftest.py` hardcodes `TEST_DB = "dentalia_test"` and opens with `DROP DATABASE ... WITH (FORCE)` + `CREATE`. A second concurrent run kills the first's connections mid-run and both report phantom failures. Setting `DENTALIA_TEST_URL` does **not** isolate you — the DROP/CREATE uses the constant. Before any `pytest`, run:
  ```bash
  docker exec dentalia-postgres psql -U dentalia -d dentalia -tAc \
    "select count(*) from pg_stat_activity where datname='dentalia_test';"
  ```
  Proceed only if it returns `0`. Tracked as `[test-db-collision]` in `tasks/followups.md`.
- **`docker compose run` outlives the CLI that started it.** After any interrupted compose run, check `docker ps` and `docker rm -f <name>`.
- **Commit from the worktree** (`.claude/worktrees/registry-views`), never from the shared checkout at `/srv/dentalia` — another session works there.
- **Current data state** (after the cold-start reset *and* the re-ingest that followed, verified 2026-08-10): `item_mirror` 15958, `item_group` 5648, `manufacturer_alias` 390, `vendor_master` 390 — but `document` and `item_document` are **0**. So the manufacturers list shows real item counts and a meaningful triage order, while the "Production docs" column is legitimately 0 for every entity until documents are fetched. Neither is a bug. Tests seed their own rows and do not depend on any of this.
- **Docs stay current in the same session** (CLAUDE.md): `docs/code-map.md` and `docs/runbook.md` updates are folded into the tasks below, not deferred.
- **Commit messages must not mention Claude or AI.**

## File Structure

| File | Responsibility |
|---|---|
| `web/registry.py` **(new)** | Queries + the four routes. Module-level query functions taking `conn` (mirrors `_status_counts(conn)` in `app.py`); one `register_routes(app, templates, conn_factory, playbooks_dir)` entry point. |
| `web/templates/manufacturers.html` **(new)** | Triage list. |
| `web/templates/manufacturer_detail.html` **(new)** | One entity: codes, items, documents, linked playbook. |
| `web/templates/playbooks.html` **(new)** | Playbook file list. |
| `web/templates/playbook_detail.html` **(new)** | One playbook rendered + raw JSON. |
| `web/app.py` **(modify)** | Two lines: import `web.registry`, call `registry.register_routes(...)` in `create_app`. |
| `web/templates/base.html` **(modify)** | Two nav links. |
| `app/config.py` **(modify)** | `Web.playbooks_dir` field + `WEB_PLAYBOOKS_DIR` env read. |
| `docker-compose.yml` **(modify)** | Read-only `./playbooks:/app/playbooks:ro` mount on `web`. |
| `tests/test_web.py` **(modify)** | New test section at end of file. |
| `docs/code-map.md`, `docs/runbook.md` **(modify)** | Keep operational docs current. Both are also touched by the `corpus-cold-start` branch (1 line each) — keep edits section-local so the branches merge cleanly. |

---

### Task 1: Manufacturers list

**Files:**
- Create: `web/registry.py`
- Create: `web/templates/manufacturers.html`
- Modify: `web/app.py` (import near line 41; one call inside `create_app`, after `templates` and `_conn` are defined — i.e. after line 411)
- Modify: `web/templates/base.html` (nav, after the `/documents` link at line 39)
- Modify: `docs/code-map.md`
- Test: `tests/test_web.py`

**Interfaces:**
- Produces: `manufacturer_rows(conn) -> list[dict]` with keys `canonical_name: str`, `codes: list[str]`, `sources: list[str]`, `items: int`, `docs: int`. `annotate_playbooks(rows, playbooks) -> list[dict]` adds `playbook_slug: str | None`. `register_routes(app, templates, conn_factory, playbooks_dir) -> None`.
- Consumes: `app.playbooks.load_playbooks(dir_path)`, `app.playbooks.Playbook`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_web.py`:

```python
# --------------------------------------------------------------------------- #
# registry views: manufacturers
# --------------------------------------------------------------------------- #
def _seed_manufacturer(conn, canonical: str, codes: list[str], source: str = "vendor-master"):
    for code in codes:
        conn.execute(
            "INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
            "VALUES (%s, %s, %s) ON CONFLICT (raw_name) DO NOTHING",
            (code, canonical, source),
        )


def _seed_item(conn, item_ref: str, manufacturer_raw: str, catalogue: str = "LJ"):
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, catalogue, updated_at) "
        "VALUES (%s, %s, %s, %s, now()) ON CONFLICT (item_ref) DO NOTHING",
        (item_ref, f"item {item_ref}", manufacturer_raw, catalogue),
    )


def test_manufacturers_groups_multi_code_entity_into_one_row(client, conn):
    _seed_manufacturer(conn, "IVOCLAR", ["001", "005", "275"])
    conn.commit()

    resp = client.get("/manufacturers")
    assert resp.status_code == 200
    assert resp.text.count(">IVOCLAR<") == 1
    for code in ("001", "005", "275"):
        assert code in resp.text


def test_manufacturers_counts_items_per_entity(client, conn):
    _seed_manufacturer(conn, "IVOCLAR", ["001", "005"])
    _seed_manufacturer(conn, "VOCO", ["042"])
    _seed_item(conn, "LJ-1", "001")
    _seed_item(conn, "LJ-2", "005")
    _seed_item(conn, "LJ-3", "042")
    conn.commit()

    rows = {r["canonical_name"]: r for r in registry.manufacturer_rows(conn)}
    assert rows["IVOCLAR"]["items"] == 2
    assert rows["VOCO"]["items"] == 1
    assert sorted(rows["IVOCLAR"]["codes"]) == ["001", "005"]


def test_manufacturers_renders_on_empty_database(client):
    resp = client.get("/manufacturers")
    assert resp.status_code == 200
    assert "Manufacturers" in resp.text


def test_manufacturers_search_matches_name_or_code(client, conn):
    _seed_manufacturer(conn, "IVOCLAR", ["001"])
    _seed_manufacturer(conn, "VOCO", ["042"])
    conn.commit()

    by_name = client.get("/manufacturers?q=ivocl")
    assert "IVOCLAR" in by_name.text and "VOCO" not in by_name.text

    by_code = client.get("/manufacturers?q=042")
    assert "VOCO" in by_code.text and "IVOCLAR" not in by_code.text


def test_manufacturers_search_treats_wildcards_literally(client, conn):
    _seed_manufacturer(conn, "IVOCLAR", ["001"])
    conn.commit()
    resp = client.get("/manufacturers?q=%25")
    assert "IVOCLAR" not in resp.text
```

Add `import json` and `from web import registry` to the imports at the top of `tests/test_web.py` (`json` is needed by Task 3's fixture; keep all imports at the top of the file, never mid-file).

- [ ] **Step 2: Run the tests to verify they fail**

Check the DB is free first (Global Constraints), then:

```bash
COMPOSE_PROJECT_NAME=dentalia docker compose --env-file /srv/dentalia/.env \
  --profile test run --rm test python -m pytest tests/test_web.py -k manufacturers -q
```

Expected: collection error — `ImportError: cannot import name 'registry' from 'web'`.

- [ ] **Step 3: Write `web/registry.py`**

```python
"""Read-only registry views: manufacturers and playbooks (S1.x).

Split out of `web/app.py` (already 1100 lines) rather than appended to it.
Same producer-only boundary: every route here is a GET, nothing writes, and
the `dentalia_api` grants would refuse a write anyway.

`app.playbooks` is imported deliberately — it is a pure data loader with no DB
or queue access, the same footing on which `web/app.py` imports `app.queue`.
It is NOT `app.handlers`/`app.workers`, which stay out of `web/` structurally.

Filtering and sorting are done in Python, not SQL: the entity set is bounded
by the catalogue's manufacturer count (~390), and playbook presence is a
filesystem fact that no SQL predicate can see, so one code path for both beats
half a filter in each.
"""

from __future__ import annotations

import json
import pathlib

from fastapi import HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from app import playbooks as playbooks_mod

# One row per canonical entity. LEFT JOINs so an entity with no items still
# appears with 0 — post-cold-start that is every entity, and a page that hid
# them would render empty and read as broken.
_MANUFACTURER_ROWS_SQL = """
SELECT a.canonical_name,
       array_agg(DISTINCT a.raw_name) AS codes,
       array_agg(DISTINCT a.source)   AS sources,
       count(DISTINCT i.item_ref)     AS items,
       count(DISTINCT ip.doc_id)      AS docs
FROM manufacturer_alias a
LEFT JOIN item_mirror i               ON i.manufacturer_raw = a.raw_name
LEFT JOIN item_document_production ip ON ip.item_ref = i.item_ref
GROUP BY a.canonical_name
"""


def manufacturer_rows(conn) -> list[dict]:
    rows = conn.execute(_MANUFACTURER_ROWS_SQL).fetchall()
    for r in rows:
        r["codes"] = sorted(r["codes"])
        r["sources"] = sorted(s for s in r["sources"] if s)
    return rows


def load_playbook_index(playbooks_dir: str | None):
    """`(playbooks, error)` — never raises. A malformed directory must render
    as a visible error row, not a 500: `load_playbooks` already swallows
    per-file parse errors by contract, so this only guards the directory."""
    try:
        path = pathlib.Path(playbooks_dir) if playbooks_dir else None
        return playbooks_mod.load_playbooks(path), None
    except Exception as exc:  # pragma: no cover - defensive
        return (), str(exc)


def annotate_playbooks(rows: list[dict], playbooks) -> list[dict]:
    """Attach `playbook_slug` per entity, entity-level: a playbook claiming any
    BC code of an entity covers the whole entity. Same union rule as
    `playbooks sync`, so the UI and the CLI cannot disagree."""
    by_name = {}
    by_code = {}
    for pb in playbooks:
        for name in pb.names():
            by_name[name.strip().upper()] = pb.slug
        for bc in pb.bc_codes:
            by_code[bc.code] = pb.slug
    for r in rows:
        slug = by_name.get(r["canonical_name"].strip().upper())
        if slug is None:
            for code in r["codes"]:
                if code in by_code:
                    slug = by_code[code]
                    break
        r["playbook_slug"] = slug
    return rows


def filter_and_sort(rows: list[dict], q: str, only_without_playbook: bool) -> list[dict]:
    """Triage order: gaps first, then biggest first, then name. Sorting on
    `items` alone would bury a large uncovered manufacturer under covered ones,
    which is the question this page exists to answer."""
    needle = q.strip().lower()
    if needle:
        rows = [
            r for r in rows
            if needle in r["canonical_name"].lower()
            or any(needle in c.lower() for c in r["codes"])
        ]
    if only_without_playbook:
        rows = [r for r in rows if r["playbook_slug"] is None]
    return sorted(
        rows,
        key=lambda r: (r["playbook_slug"] is not None, -r["items"], r["canonical_name"]),
    )


def register_routes(app, templates, conn_factory, playbooks_dir: str | None) -> None:
    @app.get("/manufacturers", response_class=HTMLResponse)
    def manufacturers(
        request: Request,
        q: str = Query(default=""),
        no_playbook: int = Query(default=0),
    ):
        pbs, pb_error = load_playbook_index(playbooks_dir)
        with conn_factory() as conn:
            rows = manufacturer_rows(conn)
        rows = annotate_playbooks(rows, pbs)
        rows = filter_and_sort(rows, q, bool(no_playbook))
        return templates.TemplateResponse(
            request,
            "manufacturers.html",
            {
                "rows": rows,
                "q": q,
                "no_playbook": bool(no_playbook),
                "playbook_error": pb_error,
                "total": len(rows),
            },
        )
```

- [ ] **Step 4: Write `web/templates/manufacturers.html`**

```html
{% extends "base.html" %}
{% block title %}Manufacturers — Dentalia Compliance Registry{% endblock %}
{% block content %}
<h1>Manufacturers</h1>
<p class="hint">
  One row per canonical entity, not per BC code — IVOCLAR's 001/005/275 are one
  manufacturer, and a playbook claiming any one of those codes covers all of them.
  Ordered for triage: entities with no playbook first, largest catalogue footprint
  first within each group.
</p>

{% if playbook_error %}
<p class="hint">Playbooks could not be read: {{ playbook_error }}</p>
{% endif %}

<div class="card">
  <form method="get" action="/manufacturers" class="filter-row">
    <div class="field">
      <label>Search (entity name or BC code)</label>
      <input type="text" name="q" value="{{ q }}" placeholder="IVOCLAR, 001, ...">
    </div>
    <div class="field">
      <label><input type="checkbox" name="no_playbook" value="1"
        {% if no_playbook %}checked{% endif %}> Only without a playbook</label>
    </div>
    <div class="field filter-actions">
      <button type="submit">Filter</button>
      <a class="btn-clear" href="/manufacturers">Clear</a>
    </div>
  </form>

  {% if rows %}
  <p class="hint">{{ total }} entit{{ "y" if total == 1 else "ies" }}.</p>
  <div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>Entity</th><th>BC codes</th><th>Items</th><th>Production docs</th>
        <th>Alias source</th><th>Playbook</th>
      </tr>
    </thead>
    <tbody>
      {% for row in rows %}
      <tr>
        <td><a href="/manufacturers/{{ row.canonical_name | urlencode }}">{{ row.canonical_name }}</a></td>
        <td>{{ row.codes | join(", ") }}</td>
        <td>{{ row.items }}</td>
        <td>{{ row.docs }}</td>
        <td>{{ row.sources | join(", ") }}</td>
        <td>
          {% if row.playbook_slug %}
          <a href="/playbooks/{{ row.playbook_slug }}">{{ row.playbook_slug }}</a>
          {% else %}—{% endif %}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  </div>
  {% else %}
  <p class="hint">No manufacturers match this filter.</p>
  {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 5: Wire it into `web/app.py`**

Add to the imports near line 41:

```python
from web import registry
```

Inside `create_app`, immediately after `_conn` is defined (after line 411):

```python
    registry.register_routes(app, templates, _conn, cfg.playbooks_dir or None)
```

Note `cfg.playbooks_dir` does not exist until Task 3. For this task use `None`:

```python
    registry.register_routes(app, templates, _conn, None)
```

- [ ] **Step 6: Add the nav link**

In `web/templates/base.html`, after the `/documents` link (line 39):

```html
        <a href="/manufacturers" class="{{ 'active' if request.url.path.startswith('/manufacturers') else '' }}">Manufacturers</a>
```

- [ ] **Step 7: Run the tests to verify they pass**

Check the DB is free, then:

```bash
COMPOSE_PROJECT_NAME=dentalia docker compose --env-file /srv/dentalia/.env \
  --profile test run --rm test python -m pytest tests/test_web.py -k manufacturers -q
```

Expected: 5 passed.

- [ ] **Step 8: Update `docs/code-map.md`**

Add `web/registry.py` to the `web/` section, described as "read-only manufacturers + playbooks views; queries and routes split out of `app.py`".

- [ ] **Step 9: Commit**

```bash
git add web/registry.py web/templates/manufacturers.html web/app.py \
        web/templates/base.html tests/test_web.py docs/code-map.md
git commit -m "web: manufacturers triage list

One row per canonical entity rather than per BC code, ordered playbook-gaps
first and largest footprint first, so the page answers which manufacturer
earns a playbook next."
```

---

### Task 2: Manufacturer detail

**Files:**
- Modify: `web/registry.py`
- Create: `web/templates/manufacturer_detail.html`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `manufacturer_rows`, `annotate_playbooks`, `load_playbook_index` from Task 1.
- Produces: `manufacturer_detail(conn, canonical_name) -> dict | None` with keys `canonical_name`, `codes` (list of dicts `{code, source, vendor_name}`), `items` (list of dicts `{item_ref, name, catalogue}`), `docs` (list of dicts `{doc_id, type, regulation, validity_to}`).

- [ ] **Step 1: Write the failing tests**

```python
def test_manufacturer_detail_lists_codes_and_items(client, conn):
    _seed_manufacturer(conn, "IVOCLAR", ["001", "005"])
    _seed_item(conn, "LJ-1", "001")
    conn.commit()

    resp = client.get("/manufacturers/IVOCLAR")
    assert resp.status_code == 200
    assert "001" in resp.text and "005" in resp.text
    assert "LJ-1" in resp.text


def test_manufacturer_detail_404_for_unknown_entity(client):
    assert client.get("/manufacturers/NOPE-DOES-NOT-EXIST").status_code == 404


def test_manufacturer_detail_handles_spaces_in_name(client, conn):
    _seed_manufacturer(conn, "3M ESPE", ["118"])
    conn.commit()

    resp = client.get("/manufacturers/3M%20ESPE")
    assert resp.status_code == 200
    assert "3M ESPE" in resp.text
```

- [ ] **Step 2: Run to verify they fail**

```bash
COMPOSE_PROJECT_NAME=dentalia docker compose --env-file /srv/dentalia/.env \
  --profile test run --rm test python -m pytest tests/test_web.py -k manufacturer_detail -q
```

Expected: FAIL — 404 on the first and third tests (route not registered).

- [ ] **Step 3: Add the query to `web/registry.py`**

```python
_DETAIL_CODES_SQL = """
SELECT a.raw_name AS code, a.source, vm.name AS vendor_name
FROM manufacturer_alias a
LEFT JOIN vendor_master vm ON vm.code = a.raw_name
WHERE a.canonical_name = %s
ORDER BY a.raw_name
"""

_DETAIL_ITEMS_SQL = """
SELECT i.item_ref, i.name, i.catalogue
FROM item_mirror i
JOIN manufacturer_alias a ON a.raw_name = i.manufacturer_raw
WHERE a.canonical_name = %s
ORDER BY i.item_ref
LIMIT 500
"""

_DETAIL_DOCS_SQL = """
SELECT DISTINCT ip.doc_id, ip.type, ip.regulation, ip.validity_to
FROM item_document_production ip
JOIN item_mirror i          ON i.item_ref = ip.item_ref
JOIN manufacturer_alias a   ON a.raw_name = i.manufacturer_raw
WHERE a.canonical_name = %s
ORDER BY ip.doc_id
LIMIT 500
"""


def manufacturer_detail(conn, canonical_name: str) -> dict | None:
    codes = conn.execute(_DETAIL_CODES_SQL, (canonical_name,)).fetchall()
    if not codes:
        return None
    return {
        "canonical_name": canonical_name,
        "codes": codes,
        "items": conn.execute(_DETAIL_ITEMS_SQL, (canonical_name,)).fetchall(),
        "docs": conn.execute(_DETAIL_DOCS_SQL, (canonical_name,)).fetchall(),
    }
```

Add the route inside `register_routes`:

```python
    @app.get("/manufacturers/{canonical_name}", response_class=HTMLResponse)
    def manufacturer_detail_page(request: Request, canonical_name: str):
        with conn_factory() as conn:
            detail = manufacturer_detail(conn, canonical_name)
        if detail is None:
            raise HTTPException(status_code=404, detail="unknown manufacturer")
        pbs, pb_error = load_playbook_index(playbooks_dir)
        row = annotate_playbooks(
            [{
                "canonical_name": canonical_name,
                "codes": [c["code"] for c in detail["codes"]],
            }],
            pbs,
        )[0]
        detail["playbook_slug"] = row["playbook_slug"]
        detail["playbook_error"] = pb_error
        return templates.TemplateResponse(request, "manufacturer_detail.html", detail)
```

- [ ] **Step 4: Write `web/templates/manufacturer_detail.html`**

```html
{% extends "base.html" %}
{% block title %}{{ canonical_name }} — Dentalia Compliance Registry{% endblock %}
{% block content %}
<h1>{{ canonical_name }}</h1>
<p class="hint">
  <a href="/manufacturers">&laquo; All manufacturers</a>
  {% if playbook_slug %} · Playbook: <a href="/playbooks/{{ playbook_slug }}">{{ playbook_slug }}</a>
  {% else %} · No playbook authored for this entity.{% endif %}
</p>

<div class="card">
  <h2>BC codes</h2>
  <div class="table-wrap">
  <table>
    <thead><tr><th>Code</th><th>Vendor-master name</th><th>Alias source</th></tr></thead>
    <tbody>
      {% for c in codes %}
      <tr><td>{{ c.code }}</td><td>{{ c.vendor_name or "—" }}</td><td>{{ c.source }}</td></tr>
      {% endfor %}
    </tbody>
  </table>
  </div>
</div>

<div class="card">
  <h2>Items ({{ items | length }})</h2>
  {% if items %}
  <div class="table-wrap">
  <table>
    <thead><tr><th>Item ref</th><th>Name</th><th>Catalogue</th></tr></thead>
    <tbody>
      {% for i in items %}
      <tr><td><a href="/items/{{ i.item_ref }}">{{ i.item_ref }}</a></td><td>{{ i.name }}</td><td>{{ i.catalogue }}</td></tr>
      {% endfor %}
    </tbody>
  </table>
  </div>
  {% else %}
  <p class="hint">No catalogue items resolve to this manufacturer yet.</p>
  {% endif %}
</div>

<div class="card">
  <h2>Production documents ({{ docs | length }})</h2>
  {% if docs %}
  <div class="table-wrap">
  <table>
    <thead><tr><th>Doc</th><th>Type</th><th>Regulation</th><th>Valid to</th></tr></thead>
    <tbody>
      {% for d in docs %}
      <tr><td><a href="/documents/{{ d.doc_id }}">{{ d.doc_id }}</a></td><td>{{ d.type }}</td><td>{{ d.regulation }}</td><td>{{ d.validity_to or "—" }}</td></tr>
      {% endfor %}
    </tbody>
  </table>
  </div>
  {% else %}
  <p class="hint">No production documents linked to this manufacturer's items.</p>
  {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
COMPOSE_PROJECT_NAME=dentalia docker compose --env-file /srv/dentalia/.env \
  --profile test run --rm test python -m pytest tests/test_web.py -k manufacturer_detail -q
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add web/registry.py web/templates/manufacturer_detail.html tests/test_web.py
git commit -m "web: manufacturer detail page

Codes with their vendor-master names, the items that resolve to the entity,
and its production documents."
```

---

### Task 3: Playbook viewer

**Files:**
- Modify: `app/config.py` (`Web` dataclass ~line 267-276; loader ~line 569-589)
- Modify: `web/registry.py`
- Modify: `web/app.py` (pass `cfg.playbooks_dir`)
- Create: `web/templates/playbooks.html`, `web/templates/playbook_detail.html`
- Modify: `web/templates/base.html` (nav)
- Modify: `docker-compose.yml` (`web` service volumes, ~line 124-126)
- Modify: `docs/runbook.md`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `load_playbook_index` from Task 1.
- Produces: `playbook_rows(playbooks_dir) -> tuple[list[dict], str | None]`, `playbook_detail(playbooks_dir, slug) -> dict | None`.

- [ ] **Step 1: Write the failing tests**

```python
# --------------------------------------------------------------------------- #
# registry views: playbooks
# --------------------------------------------------------------------------- #
@pytest.fixture
def playbook_client(test_db_url, tmp_path):
    """A client whose playbook directory is a throwaway fixture dir."""
    pb_dir = tmp_path / "playbooks"
    pb_dir.mkdir()
    (pb_dir / "voco.json").write_text(json.dumps({
        "manufacturer": "VOCO",
        "aliases": ["VOCO GmbH"],
        "bc_codes": [{"catalogue": "LJ", "code": "042"}],
        "domains": ["voco.dental"],
        "doc_sources": [{"doc_type": "DoC", "kind": "portal", "url": "https://voco.dental/doc"}],
    }))
    cfg = Web(
        api_database_url=API_TEST_URL,
        imports_dir=str(tmp_path),
        playbooks_dir=str(pb_dir),
    )
    return TestClient(create_app(cfg))


def test_playbooks_lists_authored_files(playbook_client):
    resp = playbook_client.get("/playbooks")
    assert resp.status_code == 200
    assert "voco" in resp.text
    assert "VOCO" in resp.text


def test_playbook_detail_renders_sources_and_codes(playbook_client):
    resp = playbook_client.get("/playbooks/voco")
    assert resp.status_code == 200
    assert "voco.dental" in resp.text
    assert "042" in resp.text
    assert "https://voco.dental/doc" in resp.text


def test_playbook_detail_404_for_unknown_slug(playbook_client):
    assert playbook_client.get("/playbooks/nope").status_code == 404


def test_manufacturer_row_links_its_playbook(playbook_client, conn):
    _seed_manufacturer(conn, "VOCO", ["042"])
    conn.commit()
    resp = playbook_client.get("/manufacturers")
    assert "/playbooks/voco" in resp.text


def test_playbooks_page_survives_a_malformed_file(test_db_url, tmp_path):
    pb_dir = tmp_path / "playbooks"
    pb_dir.mkdir()
    (pb_dir / "broken.json").write_text("{ not json")
    cfg = Web(
        api_database_url=API_TEST_URL,
        imports_dir=str(tmp_path),
        playbooks_dir=str(pb_dir),
    )
    client = TestClient(create_app(cfg))
    resp = client.get("/playbooks")
    assert resp.status_code == 200
```

- [ ] **Step 2: Run to verify they fail**

```bash
COMPOSE_PROJECT_NAME=dentalia docker compose --env-file /srv/dentalia/.env \
  --profile test run --rm test python -m pytest tests/test_web.py -k playbook -q
```

Expected: FAIL — `TypeError: Web.__init__() got an unexpected keyword argument 'playbooks_dir'`.

- [ ] **Step 3: Add the config field**

In `app/config.py`, add to the `Web` dataclass after `archive_root` (line 276):

```python
    playbooks_dir: str = ""
```

Add to its docstring, after the `require_authenticated_user` paragraph:

```
    `playbooks_dir` overrides where the playbook viewer reads authored JSON
    from; empty means `app.playbooks.PLAYBOOKS_DIR` (the repo-root default).
    Compose bind-mounts the directory read-only, so the UI can never write it.
```

In the loader, after the `archive_root` line (line 589):

```python
        playbooks_dir=_str("WEB_PLAYBOOKS_DIR", _dig(t, "web", "playbooks_dir"), ""),
```

- [ ] **Step 4: Add queries and routes to `web/registry.py`**

```python
def playbook_rows(playbooks_dir: str | None) -> tuple[list[dict], str | None]:
    pbs, error = load_playbook_index(playbooks_dir)
    rows = [
        {
            "slug": pb.slug,
            "manufacturer": pb.manufacturer,
            "aliases": list(pb.aliases),
            "codes": [bc.code for bc in pb.bc_codes],
            "domains": list(pb.domains),
            "source_count": len(pb.doc_sources),
        }
        for pb in pbs
    ]
    return sorted(rows, key=lambda r: r["slug"]), error


def playbook_detail(playbooks_dir: str | None, slug: str) -> dict | None:
    pbs, error = load_playbook_index(playbooks_dir)
    pb = next((p for p in pbs if p.slug == slug), None)
    if pb is None:
        return None
    root = pathlib.Path(playbooks_dir) if playbooks_dir else playbooks_mod.PLAYBOOKS_DIR
    try:
        raw = json.dumps(json.loads((root / f"{slug}.json").read_text()), indent=2)
    except Exception as exc:
        raw = f"(could not re-read the file: {exc})"
    return {
        "slug": pb.slug,
        "manufacturer": pb.manufacturer,
        "aliases": list(pb.aliases),
        "codes": [f"{bc.catalogue}/{bc.code}" for bc in pb.bc_codes],
        "domains": list(pb.domains),
        "doc_sources": [
            {"doc_type": s.doc_type, "kind": s.kind, "url": s.url, "note": s.note}
            for s in pb.doc_sources
        ],
        "raw": raw,
        "playbook_error": error,
    }
```

Add both routes inside `register_routes`:

```python
    @app.get("/playbooks", response_class=HTMLResponse)
    def playbooks_page(request: Request):
        rows, error = playbook_rows(playbooks_dir)
        return templates.TemplateResponse(
            request,
            "playbooks.html",
            {"rows": rows, "playbook_error": error},
        )

    @app.get("/playbooks/{slug}", response_class=HTMLResponse)
    def playbook_detail_page(request: Request, slug: str):
        detail = playbook_detail(playbooks_dir, slug)
        if detail is None:
            raise HTTPException(status_code=404, detail="unknown playbook")
        return templates.TemplateResponse(request, "playbook_detail.html", detail)
```

- [ ] **Step 5: Pass the config through in `web/app.py`**

Replace the Task 1 registration line with:

```python
    registry.register_routes(app, templates, _conn, cfg.playbooks_dir or None)
```

- [ ] **Step 6: Write `web/templates/playbooks.html`**

```html
{% extends "base.html" %}
{% block title %}Playbooks — Dentalia Compliance Registry{% endblock %}
{% block content %}
<h1>Playbooks</h1>
<p class="hint">
  Authored per-manufacturer config: BC codes, aliases, official domains and document
  sources. Read-only here — edit the JSON in the repo and apply it with
  <code>python -m app.cli playbooks validate</code> then <code>playbooks sync</code>.
</p>

{% if playbook_error %}
<p class="hint">Playbooks could not be read: {{ playbook_error }}</p>
{% endif %}

<div class="card">
  {% if rows %}
  <div class="table-wrap">
  <table>
    <thead>
      <tr><th>Slug</th><th>Manufacturer</th><th>Aliases</th><th>BC codes</th><th>Domains</th><th>Doc sources</th></tr>
    </thead>
    <tbody>
      {% for row in rows %}
      <tr>
        <td><a href="/playbooks/{{ row.slug }}">{{ row.slug }}</a></td>
        <td>{{ row.manufacturer }}</td>
        <td>{{ row.aliases | join(", ") or "—" }}</td>
        <td>{{ row.codes | join(", ") or "—" }}</td>
        <td>{{ row.domains | join(", ") or "—" }}</td>
        <td>{{ row.source_count }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  </div>
  {% else %}
  <p class="hint">No playbooks found.</p>
  {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 7: Write `web/templates/playbook_detail.html`**

```html
{% extends "base.html" %}
{% block title %}{{ slug }} — Playbook{% endblock %}
{% block content %}
<h1>{{ slug }}</h1>
<p class="hint">
  <a href="/playbooks">&laquo; All playbooks</a> · Read-only. Edit
  <code>playbooks/{{ slug }}.json</code> in the repo, then
  <code>python -m app.cli playbooks validate</code> and <code>playbooks sync</code>.
</p>

{% if playbook_error %}
<p class="hint">{{ playbook_error }}</p>
{% endif %}

<div class="card">
  <h2>Identity</h2>
  <table>
    <tbody>
      <tr><th>Manufacturer</th><td>{{ manufacturer }}</td></tr>
      <tr><th>Aliases</th><td>{{ aliases | join(", ") or "—" }}</td></tr>
      <tr><th>BC codes</th><td>{{ codes | join(", ") or "—" }}</td></tr>
      <tr><th>Domains</th><td>{{ domains | join(", ") or "—" }}</td></tr>
    </tbody>
  </table>
</div>

<div class="card">
  <h2>Document sources ({{ doc_sources | length }})</h2>
  {% if doc_sources %}
  <div class="table-wrap">
  <table>
    <thead><tr><th>Type</th><th>Kind</th><th>URL</th><th>Note</th></tr></thead>
    <tbody>
      {% for s in doc_sources %}
      <tr><td>{{ s.doc_type }}</td><td>{{ s.kind }}</td><td>{{ s.url }}</td><td>{{ s.note or "—" }}</td></tr>
      {% endfor %}
    </tbody>
  </table>
  </div>
  {% else %}
  <p class="hint">No document sources authored.</p>
  {% endif %}
</div>

<div class="card">
  <details>
    <summary>Raw JSON</summary>
    <pre>{{ raw }}</pre>
  </details>
</div>
{% endblock %}
```

- [ ] **Step 8: Add the nav link**

In `web/templates/base.html`, after the `/manufacturers` link added in Task 1:

```html
        <a href="/playbooks" class="{{ 'active' if request.url.path.startswith('/playbooks') else '' }}">Playbooks</a>
```

- [ ] **Step 9: Mount `playbooks/` into the web container**

In `docker-compose.yml`, in the `web` service `volumes:` block (after the `archive_data` line, ~line 126):

```yaml
      # Read-only: the playbook viewer renders these, and the producer UI must
      # never write an authored file. A bind mount rather than a Dockerfile.web
      # COPY so an edited playbook shows without an image rebuild.
      - ./playbooks:/app/playbooks:ro
```

- [ ] **Step 10: Run the tests to verify they pass**

```bash
COMPOSE_PROJECT_NAME=dentalia docker compose --env-file /srv/dentalia/.env \
  --profile test run --rm test python -m pytest tests/test_web.py -k "playbook or manufacturer" -q
```

Expected: 13 passed.

- [ ] **Step 11: Update `docs/runbook.md`**

In the UI section (around line 32, which describes `http://127.0.0.1:8000`), add the new routes to the page list, and add this note:

```markdown
`docker compose up -d` does NOT rebuild a changed image. After editing `web/`
or `app/`, use `docker compose up -d --build web`, or the container keeps
serving the old code — a stale `dentalia-web` image will 404 on routes that
exist in the source while still reporting healthy.
```

- [ ] **Step 12: Commit**

```bash
git add app/config.py web/registry.py web/app.py web/templates/playbooks.html \
        web/templates/playbook_detail.html web/templates/base.html \
        docker-compose.yml tests/test_web.py docs/runbook.md
git commit -m "web: read-only playbook viewer

Renders authored playbooks/*.json with their codes, domains and document
sources, linked both ways with the manufacturers list. The container mount is
read-only: applying an edit needs playbooks sync, which writes manufacturer_alias
and is not something the producer UI is granted."
```

---

### Task 4: Full verification

**Files:** none changed unless a defect is found.

- [ ] **Step 1: Confirm the test database is free**

```bash
docker exec dentalia-postgres psql -U dentalia -d dentalia -tAc \
  "select count(*) from pg_stat_activity where datname='dentalia_test';"
```

Expected: `0`. If not, stop and coordinate — do not start the suite.

- [ ] **Step 2: Run the whole suite**

```bash
COMPOSE_PROJECT_NAME=dentalia docker compose --env-file /srv/dentalia/.env \
  --profile test run --rm test 2>&1 | tail -20
```

Expected: all tests pass, no errors. Read the failure *shape* before believing a red: suite-wide "undefined table" errors across untouched tests mean a collision, not a regression.

- [ ] **Step 3: Confirm no container was orphaned**

```bash
docker ps --format '{{.Names}}'
```

Expected: `dentalia-web`, `dentalia-caddy`, `dentalia-postgres` only. `docker rm -f` anything named `dentalia-test-run-*`.

- [ ] **Step 4: Drive the real app**

```bash
docker compose up -d --build web
docker compose exec -T web python - <<'PY'
import urllib.request
for path in ["/manufacturers", "/manufacturers?no_playbook=1", "/playbooks"]:
    req = urllib.request.Request("http://127.0.0.1:8000" + path,
                                 headers={"X-Forwarded-User": "denis"})
    r = urllib.request.urlopen(req, timeout=20)
    print(r.status, path, len(r.read()), "bytes")
PY
```

Expected: three 200s. Then fetch one entity detail and one playbook detail by name from the rendered list and confirm 200.

- [ ] **Step 5: Confirm the mount is genuinely read-only**

```bash
docker compose exec -T web sh -c 'touch /app/playbooks/x 2>&1 || echo "read-only: correct"'
```

Expected: `read-only: correct`.

- [ ] **Step 6: Commit any fixes, then report**

Report: routes added, test count, and the fact that item/doc columns read 0 for every entity until the cold-start re-ingest lands.

---

## Self-Review

**Spec coverage:** §1 data → Task 1 Step 3 and Task 2 Step 3. §2 routes → all four implemented (Tasks 1–3). §3 code placement → `web/registry.py`, `app.py` +2 lines. §4 compose → Task 3 Step 9. §6 testing → all seven listed cases have tests (entity grouping T1, no-playbook filter T3 via `no_playbook`, counts T1, detail 200/404 T2, playbook list T3, playbook detail + 404 T3, malformed file T3). §7 edge cases → spaces in names T2, mixed alias sources rendered via `sources` join, zero-item entities T1 `renders_on_empty_database`. §8 docs → folded into Tasks 1 and 3.

**Deviation from the spec, deliberate:** the spec left the default sort ambiguous ("items desc, playbook-less first"). Locked as `(has_playbook, -items, name)` — gaps first, largest first within each group — since burying a large uncovered manufacturer beneath covered ones defeats the page's purpose. Recorded in `filter_and_sort`'s docstring.

**Placeholder scan:** none. Every step carries real code.

**Type consistency:** `manufacturer_rows` emits `codes: list[str]`; `annotate_playbooks` and `filter_and_sort` consume that shape; `manufacturer_detail` emits `codes` as a list of dicts and the detail route converts to `list[str]` before calling `annotate_playbooks`. `load_playbook_index` returns `(playbooks, error)` everywhere.

**Known gap:** `app/config.py` is a third file also touched on `corpus-cold-start` (they edited the `Discovery` section, this touches `Web`), so a merge conflict is unlikely but possible.
