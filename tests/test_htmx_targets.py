"""Every `hx-target` id selector must be usable and must resolve.

Both scheduler bugs of 2026-09-02 lived here and neither was reachable from a
route test. `hx-target="#run-result-report.weekly"` is not "the element whose id
is run-result-report.weekly" -- CSS reads it as id `run-result-report` plus class
`weekly`, matches nothing, and htmx raises `htmx:targetError` and ABORTS WITHOUT
ISSUING THE REQUEST. The POST route was correct the whole time, which is exactly
why 2884 passing tests said nothing. The second bug was a target whose element
rendered only inside an `{% if %}` the form sat outside of.

Static, not rendered: it reads the template sources, so it covers pages no test
happens to load and templates written later.
"""

from __future__ import annotations

import pathlib
import re

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "web" / "templates"

_TARGET = re.compile(r'hx-target="#([^"]+)"')
_ID = re.compile(r'id="([^"]+)"')

#: Interpolated targets are fine when the value cannot contain a dot. Object ids
#: and loop counters are integers; `confirm_target` is only ever a literal id
#: ("rename-result", "code-result", "bc-push-result") or "bind-confirm-" plus an
#: integer doc id, never user text: `_result.html` also puts it in a JS string.
_SAFE_INTERPOLATION = re.compile(
    r"""\{\{\s*(
        [a-z_]+\.id            # job.id, task.id, s.id
      | loop\.index
      | confirm_target
      | [a-z_.]+\s*\|\s*replace\(\s*['"]\.['"]\s*,\s*['"]-['"]\s*\)
    )\s*\}\}""",
    re.X,
)


def _sources() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in TEMPLATES.glob("*.html")}


def test_no_interpolated_target_can_produce_a_dotted_id():
    """A value carrying a dot must be filtered before it reaches the selector."""
    bad = []
    for name, src in _sources().items():
        for target in _TARGET.findall(src):
            if "{{" not in target:
                continue
            if not _SAFE_INTERPOLATION.search(target):
                bad.append(f"{name}: {target}")
    assert bad == [], (
        "hx-target interpolates a value that may contain a dot; filter it with "
        "| replace('.', '-') as scheduler.html does — " + "; ".join(bad)
    )


def test_every_literal_target_resolves_to_an_element_that_exists():
    """The id may live in another template (a fragment swapped into a page), so
    the search is repo-wide rather than per-file."""
    srcs = _sources()
    all_ids = {i for src in srcs.values() for i in _ID.findall(src)}
    missing = []
    for name, src in srcs.items():
        for target in _TARGET.findall(src):
            if "{{" in target:
                continue
            if target not in all_ids:
                missing.append(f"{name}: #{target}")
    assert missing == [], (
        "hx-target points at an id no template renders — htmx will abort the "
        "request: " + "; ".join(missing)
    )
