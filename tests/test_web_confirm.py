"""Task 0: the generic two-step confirm contract.

`_result.html`'s `confirm_needed` branch used to be hard-wired to Rename
(hidden `new_name`/`note`, button "Yes, rename it"). It is now generic:
`confirm_fields` is a dict rendered as one hidden input per key, and
`confirm_label` is the button text -- so any two-step action can reuse the
same branch instead of hard-coding its own fields (spec § 3). A Cancel
control clears `#confirm_target` without issuing a request.

These tests pin the contract itself (rendering `_result.html` directly, with
no route or database needed) and then confirm the one caller that already
uses it -- rename -- still gets its own exact wording and fields through the
now-generic branch.
"""

from __future__ import annotations

import pathlib

import jinja2
import pytest
from fastapi.testclient import TestClient

from app.config import Web
from tests.conftest import TEST_API_URL
from web.app import create_app

_TEMPLATES_DIR = pathlib.Path(__file__).resolve().parents[1] / "web" / "templates"


def _render_result(**ctx) -> str:
    """Render `_result.html` in isolation, the same way `web/app.py`'s
    `Jinja2Templates(directory=...)` would, without needing a request, a
    route or a database."""
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)), autoescape=True)
    return env.get_template("_result.html").render(**ctx)


@pytest.fixture
def client(test_db_url, tmp_path):
    return TestClient(create_app(Web(api_database_url=TEST_API_URL,
                                     imports_dir=str(tmp_path))))


# --------------------------------------------------------------------------- #
# the generic contract, rendered directly
# --------------------------------------------------------------------------- #
def test_confirm_fields_render_as_one_hidden_input_per_key():
    html = _render_result(
        confirm_needed=["Do the thing?"],
        confirm_url="/things/1/do",
        confirm_target="thing-result",
        confirm_fields={"a": "1", "b": "x y"},
        confirm_label="Yes, do it",
    )
    assert '<input type="hidden" name="a" value="1">' in html
    assert '<input type="hidden" name="b" value="x y">' in html


def test_confirm_always_carries_the_hidden_confirm_flag():
    html = _render_result(
        confirm_needed=["Do the thing?"],
        confirm_url="/things/1/do",
        confirm_target="thing-result",
        confirm_fields={"a": "1"},
        confirm_label="Yes, do it",
    )
    assert '<input type="hidden" name="confirm" value="1">' in html


def test_confirm_label_is_the_button_text():
    html = _render_result(
        confirm_needed=["Do the thing?"],
        confirm_url="/things/1/do",
        confirm_target="thing-result",
        confirm_fields={"a": "1"},
        confirm_label="Yes, do it",
    )
    assert "<button type=\"submit\">Yes, do it</button>" in html


def test_a_cancel_control_clears_the_target_without_a_request():
    """No `hx-post`/`hx-get` on the Cancel control -- it must not issue a
    request. It names the same element `confirm_target` points at, so
    cancelling clears exactly what the confirm form would have replaced."""
    html = _render_result(
        confirm_needed=["Do the thing?"],
        confirm_url="/things/1/do",
        confirm_target="thing-result",
        confirm_fields={"a": "1"},
        confirm_label="Yes, do it",
    )
    assert "Cancel" in html
    cancel_line = next(line for line in html.splitlines() if "Cancel" in line)
    assert "hx-post" not in cancel_line and "hx-get" not in cancel_line
    assert "onclick" in cancel_line
    assert "thing-result" in cancel_line


# --------------------------------------------------------------------------- #
# rename, still through the now-generic branch
# --------------------------------------------------------------------------- #
def test_rename_keeps_its_own_wording_through_the_generic_contract(conn, client):
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES (%s)",
        ("CONFIRM TEST GMBH",))
    conn.commit()

    r = client.post("/manufacturers/CONFIRM TEST GMBH/rename",
                    data={"new_name": "CONFIRM TEST NEW GMBH", "note": "why"})

    assert r.status_code == 200
    assert "Yes, rename it" in r.text
    assert ('<input type="hidden" name="new_name" '
            'value="CONFIRM TEST NEW GMBH">') in r.text
    assert '<input type="hidden" name="note" value="why">' in r.text
    assert "Cancel" in r.text
