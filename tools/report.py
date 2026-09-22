"""Output writers shared by the analyzers: rows -> CSV, dict -> JSON,
sections -> markdown.

Deliberately thin — no templating engine. The run date is a parameter, never
read from the clock here, so a report is a pure function of its inputs and
re-running an analyzer on unchanged data yields byte-identical files.
"""

from __future__ import annotations

import csv
import json
import pathlib
from collections.abc import Iterable, Sequence


def _prepare(path) -> pathlib.Path:
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def write_csv(path, rows: Iterable[dict], columns: Sequence[str]) -> pathlib.Path:
    """Write `rows` with exactly `columns`, in that order."""
    path = _prepare(path)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_json(path, payload) -> pathlib.Path:
    """Sorted keys: two equal payloads must produce identical bytes."""
    path = _prepare(path)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _cell(value) -> str:
    return str(value).replace("|", "\\|")


def table(headers: Sequence[str], rows: Iterable[Sequence]) -> str:
    """GitHub-flavoured markdown table. Pipes in values are escaped."""
    lines = [
        "| " + " | ".join(_cell(h) for h in headers) + " |",
        "|" + "---|" * len(headers),
    ]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def write_markdown(
    path, *, title: str, date: str, intro: str, sections: Iterable[tuple[str, str]]
) -> pathlib.Path:
    """`sections` is an ordered sequence of (heading, body)."""
    path = _prepare(path)
    parts = [f"# {title}", "", f"{date} · {intro}", ""]
    for heading, body in sections:
        parts += [f"## {heading}", "", body, ""]
    path.write_text("\n".join(parts).rstrip() + "\n", encoding="utf-8")
    return path
