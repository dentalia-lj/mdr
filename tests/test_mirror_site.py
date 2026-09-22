"""scripts/mirror-mdr-site.py: index parsing, path safety, conditional fetch.

No network and no database. The index is someone else's CMS output, so the
parse has to reject what it cannot place rather than write it somewhere
surprising, and the fetch has to leave no half file behind when it fails.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load():
    path = _ROOT / "scripts" / "mirror-mdr-site.py"
    spec = importlib.util.spec_from_file_location("mirror_mdr_site", path)
    mod = importlib.util.module_from_spec(spec)
    # `dataclasses` resolves annotations through sys.modules, so a module loaded
    # by path has to be registered before its body runs.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


mirror = _load()


def _file(name: str, path: str, parents: list[str]) -> dict:
    return {"name": name, "path": path, "parents": parents}


SAMPLE = {
    "GC": {
        "DOC": {
            "a.pdf": _file("a.pdf", "/api/files/GC/DOC/a.pdf", ["GC", "DOC"]),
        },
        "top.pdf": _file("top.pdf", "/api/files/GC/top.pdf", ["GC"]),
    },
    "3M-ZDAJ SOLVENTUM": {
        "b c.pdf": _file("b c.pdf", "/api/files/3M-ZDAJ%20SOLVENTUM/b%20c.pdf",
                         ["3M-ZDAJ SOLVENTUM"]),
    },
}


# --- parse ------------------------------------------------------------------

def test_parse_index_flattens_folders_and_files():
    entries, rejected = mirror.parse_index(SAMPLE)
    assert rejected == []
    assert sorted(e.relpath for e in entries) == [
        "3M-ZDAJ SOLVENTUM/b c.pdf", "GC/DOC/a.pdf", "GC/top.pdf"]
    by_rel = {e.relpath: e for e in entries}
    assert by_rel["GC/DOC/a.pdf"].brand == "GC"
    # the url keeps the index's own encoding; the local path is the plain name
    assert by_rel["3M-ZDAJ SOLVENTUM/b c.pdf"].url_path == \
        "/api/files/3M-ZDAJ%20SOLVENTUM/b%20c.pdf"


def test_parse_index_rejects_traversal_without_dropping_the_run():
    idx = {"GC": {"x": _file("../../etc/passwd", "/api/files/GC/x", ["GC"]),
                  "ok.pdf": _file("ok.pdf", "/api/files/GC/ok.pdf", ["GC"])}}
    entries, rejected = mirror.parse_index(idx)
    assert [e.relpath for e in entries] == ["GC/ok.pdf"]
    assert len(rejected) == 1 and "unsafe" in rejected[0]


def test_parse_index_rejects_a_path_outside_the_files_prefix():
    idx = {"GC": {"x.pdf": _file("x.pdf", "https://evil.example/x.pdf", ["GC"]),
                  "ok.pdf": _file("ok.pdf", "/api/files/GC/ok.pdf", ["GC"])}}
    entries, rejected = mirror.parse_index(idx)
    assert [e.relpath for e in entries] == ["GC/ok.pdf"]
    assert "outside" in rejected[0]


def test_parse_index_rejects_a_file_above_any_brand_folder():
    idx = {"loose.pdf": _file("loose.pdf", "/api/files/loose.pdf", []),
           "GC": {"ok.pdf": _file("ok.pdf", "/api/files/GC/ok.pdf", ["GC"])}}
    entries, rejected = mirror.parse_index(idx)
    assert [e.relpath for e in entries] == ["GC/ok.pdf"]
    assert "above any brand" in rejected[0]


@pytest.mark.parametrize("payload", [{}, [], "", {"GC": {}}])
def test_parse_index_refuses_a_shape_it_does_not_know(payload):
    with pytest.raises(mirror.MirrorError):
        mirror.parse_index(payload)


@pytest.mark.parametrize("part", ["", ".", "..", "a/b", "a\\b", "a\x00b", "x" * 256])
def test_check_component_rejects(part):
    with pytest.raises(ValueError):
        mirror.check_component(part)


def test_check_component_accepts_the_real_shapes():
    for part in ["GC", "3M-ZDAJ SOLVENTUM", "DOC Filtek Easy Match.pdf",
                 "BLUE M - OXYGEN FLUID AND ORAL GEL (other not MD)", "č š ž.pdf"]:
        mirror.check_component(part)


def test_importable_is_backfills_pdf_only_walk():
    def e(name):
        return mirror.Entry(brand="GC", parts=("GC", name), url_path="/api/files/GC/" + name)
    assert mirror.importable(e("a.pdf"))
    assert mirror.importable(e("A.PDF"))
    # the three 3M files whose extension really is `pdf_`
    assert not mirror.importable(e("a.pdf_"))
    assert not mirror.importable(e("a.docx"))


def test_http_date_round_trips():
    ts = 1_600_000_000.0
    assert mirror.parse_http_date(mirror.http_date(ts)) == ts
    assert mirror.parse_http_date(None) is None
    assert mirror.parse_http_date("not a date") is None


# --- fetch ------------------------------------------------------------------

class _Resp:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None):
        self._body = body
        self._pos = 0
        self.headers = headers or {}

    def read(self, n=-1):
        chunk = self._body[self._pos:] if n is None or n < 0 else self._body[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _headers(last_modified: str | None = None):
    return {"Last-Modified": last_modified} if last_modified else {}


@pytest.fixture()
def no_sleep(monkeypatch):
    monkeypatch.setattr(mirror.time, "sleep", lambda *_: None)


def test_download_new_file_writes_bytes_and_takes_the_servers_mtime(tmp_path, monkeypatch):
    seen = {}

    def fake_open(url, headers, timeout):
        seen["url"], seen["headers"] = url, dict(headers)
        return _Resp(b"%PDF-1.7 body", _headers("Tue, 07 Apr 2026 10:17:40 GMT"))

    monkeypatch.setattr(mirror, "_open", fake_open)
    dest = tmp_path / "GC" / "DOC" / "a.pdf"
    outcome, n = mirror.download("https://x/api/files/GC/DOC/a.pdf", dest,
                                 timeout=5, retries=0, delay=0)
    assert (outcome, n) == ("new", 13)
    assert dest.read_bytes() == b"%PDF-1.7 body"
    assert dest.stat().st_mtime == mirror.parse_http_date("Tue, 07 Apr 2026 10:17:40 GMT")
    assert "If-Modified-Since" not in seen["headers"]      # nothing to be modified since
    assert list(dest.parent.glob(".*")) == []              # no leftover part file


def test_download_sends_if_modified_since_and_honours_304(tmp_path, monkeypatch):
    import urllib.error

    dest = tmp_path / "GC" / "a.pdf"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"old")
    import os
    os.utime(dest, (1_600_000_000, 1_600_000_000))
    seen = {}

    def fake_open(url, headers, timeout):
        seen.update(headers)
        raise urllib.error.HTTPError(url, 304, "Not Modified", {}, None)

    monkeypatch.setattr(mirror, "_open", fake_open)
    outcome, n = mirror.download("https://x/api/files/GC/a.pdf", dest,
                                 timeout=5, retries=0, delay=0)
    assert (outcome, n) == ("unchanged", 0)
    assert dest.read_bytes() == b"old"
    assert seen["If-Modified-Since"] == mirror.http_date(1_600_000_000)


def test_download_replaces_a_changed_file(tmp_path, monkeypatch):
    dest = tmp_path / "GC" / "a.pdf"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"old")
    monkeypatch.setattr(mirror, "_open",
                        lambda url, headers, timeout: _Resp(b"new bytes", _headers()))
    outcome, n = mirror.download("https://x/a.pdf", dest, timeout=5, retries=0, delay=0)
    assert (outcome, n) == ("updated", 9)
    assert dest.read_bytes() == b"new bytes"


def test_download_retries_a_503_then_succeeds(tmp_path, monkeypatch, no_sleep):
    import urllib.error
    calls = {"n": 0}

    def fake_open(url, headers, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(url, 503, "busy", {}, None)
        return _Resp(b"ok", _headers())

    monkeypatch.setattr(mirror, "_open", fake_open)
    dest = tmp_path / "GC" / "a.pdf"
    assert mirror.download("https://x/a.pdf", dest, timeout=5, retries=3, delay=0) == ("new", 2)
    assert calls["n"] == 2


def test_download_does_not_retry_a_404_and_leaves_nothing_behind(tmp_path, monkeypatch):
    import urllib.error
    calls = {"n": 0}

    def fake_open(url, headers, timeout):
        calls["n"] += 1
        raise urllib.error.HTTPError(url, 404, "gone", {}, None)

    monkeypatch.setattr(mirror, "_open", fake_open)
    dest = tmp_path / "GC" / "a.pdf"
    with pytest.raises(urllib.error.HTTPError):
        mirror.download("https://x/a.pdf", dest, timeout=5, retries=3, delay=0)
    assert calls["n"] == 1
    assert not dest.exists()
    assert list((tmp_path / "GC").iterdir()) == []


def test_download_leaves_the_old_file_intact_when_the_body_dies_mid_stream(tmp_path, monkeypatch):
    dest = tmp_path / "GC" / "a.pdf"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"good old bytes")

    class _Broken(_Resp):
        def read(self, n=-1):
            raise ConnectionResetError("peer hung up")

    monkeypatch.setattr(mirror, "_open",
                        lambda url, headers, timeout: _Broken(b"", _headers()))
    with pytest.raises(OSError):
        mirror.download("https://x/a.pdf", dest, timeout=5, retries=0, delay=0)
    assert dest.read_bytes() == b"good old bytes"
    assert list(dest.parent.glob(".*")) == []


# --- orphans ----------------------------------------------------------------

def test_find_orphans_lists_only_unknown_files_under_scanned_brands(tmp_path):
    entries, _ = mirror.parse_index(SAMPLE)
    for e in entries:
        p = tmp_path / e.relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    (tmp_path / "GC" / "stale.pdf").write_bytes(b"x")
    (tmp_path / "GC" / ".a.pdf.part").write_bytes(b"x")       # in-flight, not an orphan
    (tmp_path / "OTHER").mkdir()
    (tmp_path / "OTHER" / "untouched.pdf").write_bytes(b"x")  # brand not in scope

    orphans = mirror.find_orphans(tmp_path, entries, {"GC"})
    assert [p.relative_to(tmp_path).as_posix() for p in orphans] == ["GC/stale.pdf"]
