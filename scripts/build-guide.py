#!/usr/bin/env python3
"""Render docs/guide/*.md to HTML styled as the Dentalia web UI.

Two output shapes, both offline (no network call, ever):

  docs/guide/html/{en,sl}/...        one page per file, sharing ../assets/
  docs/guide/dentalia-guide-EN.html  every page in one file, assets inlined

"Self-contained" means no network call, not necessarily one file. The
per-page tree shares an asset folder because the five font faces are ~191 KB
base64 and inlining them into forty pages would be 8 MB of duplication; the
single-file bundle pays that once, for emailing and printing.

No dependencies. The guide uses a closed subset of Markdown — headings,
paragraphs, tables, bullet and ordered lists, horizontal rules, and four inline
forms — so a stdlib parser covers it and the repo gains no dependency for a docs
tool. Anything outside that subset is reported rather than silently dropped.

Styling is lifted from web/static/css/style.css: the dentalia.si mint palette,
Sora and Work Sans, the app's sidebar. The guide should look like the product it
documents. This file is the only copy of that CSS outside the app — do not
hand-edit generated HTML.

Usage:
    python3 scripts/build-guide.py                 # both languages, both shapes
    python3 scripts/build-guide.py --lang sl       # one language
    python3 scripts/build-guide.py --check         # parse only, write nothing
"""

from __future__ import annotations

import argparse
import base64
import html
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUIDE = ROOT / "docs" / "guide"
OUT = GUIDE / "html"
STATIC = ROOT / "web" / "static"

FONT_FILES = [
    ("Sora", "normal", "400 800", "sora-latin.woff2", "latin"),
    ("Sora", "normal", "400 800", "sora-latin-ext.woff2", "ext"),
    ("Work Sans", "normal", "400 600", "worksans-normal-latin.woff2", "latin"),
    ("Work Sans", "normal", "400 600", "worksans-normal-latin-ext.woff2", "ext"),
    ("Work Sans", "italic", "400", "worksans-italic-latin.woff2", "latin"),
]

RANGES = {
    "latin": (
        "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,"
        "U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,"
        "U+FEFF,U+FFFD"
    ),
    # Slovenian needs this one for č š ž, so neither language can drop it.
    "ext": (
        "U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,"
        "U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,"
        "U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF"
    ),
}

# Sidebar order mirrors web/templates/base.html so "where I am in the app" and
# "where I am in the guide" line up. Every page under `pages/` must appear here:
# `sources()` globs the directory, so an unlisted page is still BUILT into the
# bundle and is simply unreachable from the sidebar -- which is how `search` and
# `bc-push` sat in both bundles, in neither menu, until 2026-09-07.
# `tests/test_docs_sets.py::test_every_guide_page_is_in_the_sidebar` is the guard.
NAV: list[tuple[str, list[tuple[str, str]]]] = [
    ("", [("Start here", "00-getting-started"), ("Daily round", "01-daily-work"),
          ("Today", "pages/today"), ("Search", "pages/search")]),
    ("Registry", [
        ("Items", "pages/items"), ("Documents", "pages/documents"),
        ("Manufacturers", "pages/manufacturers"),
        ("EUDAMED checks", "pages/eudamed"),
        ("Playbooks", "pages/playbooks"),
        ("Onboard a supplier", "pages/onboarding"),
        ("Coverage gaps", "pages/coverage"), ("Discovery", "pages/discovery"),
        ("Expiry", "pages/expiry"), ("Review", "pages/review"),
        ("Missing documents", "pages/missing"),
        ("Decisions", "pages/decisions"),
    ]),
    # Named as the app's own menu names them (office UI redesign spec § 9):
    # "Emails in" and "Drafts out" were sidebar-only words for screens the
    # menu calls Emails received and Renewal emails. The page STEMS are
    # unchanged -- a file name is an address, and every cross-link in both
    # languages points at it.
    ("Correspondence", [
        ("Emails received", "pages/emails-in"),
        ("Renewal emails", "pages/drafts-out"),
    ]),
    # Mirrors base.html after 2026-09-04: the four that stayed in the menu, then
    # the hub, then the five it folded, then Ingest -- which is documented here
    # but deliberately absent from the app's menu (`[ingest-page-says-the-import-
    # does-nothing]`). The guide keeps listing all of them: it is reference, not
    # navigation, and a page you can still reach needs a page you can still read.
    ("Pipeline", [
        ("System status", "pages/status"),
        ("Import from Business Central", "pages/import"),
        ("Upload a document", "pages/upload"),
        ("Weekly reports", "pages/reports"),
        ("Queues & health", "pages/pipeline"),
        ("Processing", "pages/processing"),
        ("Data quality", "pages/data-quality"), ("Manual", "pages/manual"),
        ("Failed", "pages/failed"), ("Scheduler", "pages/scheduler"),
        ("Business Central", "pages/bc-push"),
        ("Ingest", "pages/ingest"),
    ]),
    ("Reference", [("Glossary", "glossary")]),
]

TITLES = {"en": "Compliance guide", "sl": "Priročnik"}


# --------------------------------------------------------------------------- #
# markdown — the closed subset the guide uses
# --------------------------------------------------------------------------- #

def slug(text: str) -> str:
    """GitHub-style heading anchor, matching what the .md cross-links assume."""
    t = re.sub(r"`([^`]*)`", r"\1", text)
    t = re.sub(r"\*\*?([^*]*)\*\*?", r"\1", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"[^\w\s-]", "", t.lower()).strip()
    return re.sub(r"\s+", "-", t)


def inline(text: str) -> str:
    """Bold, italic, code and links. Escapes first, so source text is literal."""
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`]+)`", lambda m: f"<code>{m.group(1)}</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", out)

    def link(m: re.Match) -> str:
        label, href = m.group(1), m.group(2)
        # .md -> .html, and .sl.md -> .html because each language has its own
        # directory. Anything else (an anchor, an external path) is left alone.
        href = re.sub(r"\.sl\.md(?=#|$)", ".html", href)
        href = re.sub(r"\.md(?=#|$)", ".html", href)
        return f'<a href="{html.escape(href, quote=True)}">{label}</a>'

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, out)


def render(md: str, unsupported: list[str]) -> tuple[str, str]:
    """Return (html, first h1 text). Records anything outside the subset."""
    lines = md.splitlines()
    out: list[str] = []
    title = ""
    i = 0
    # A list may only START at a block boundary. Without this, a wrapped
    # sentence whose continuation line begins with a number and a period --
    # e.g. the Slovene date "18. 8. 2026" -- is parsed as an ordered list,
    # which shatters the paragraph and eats the day.
    block_start = True

    while i < len(lines):
        line = lines[i]

        if not line.strip():
            block_start = True
            i += 1
            continue

        if line.startswith("```"):
            unsupported.append(f"fenced code block at line {i + 1}")
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                i += 1
            i += 1
            continue

        if re.match(r"^-{3,}$", line.strip()):
            out.append("<hr>")
            i += 1
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level, text = len(m.group(1)), m.group(2).strip()
            if level == 1 and not title:
                title = re.sub(r"[*`]", "", text)
            out.append(f'<h{level} id="{slug(text)}">{inline(text)}</h{level}>')
            i += 1
            continue

        # table: header row, separator, body
        if line.startswith("|") and i + 1 < len(lines) and re.match(
            r"^\|[\s:|-]+\|?$", lines[i + 1]
        ):
            head = [c.strip() for c in line.strip().strip("|").split("|")]
            i += 2
            body = []
            while i < len(lines) and lines[i].startswith("|"):
                body.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            out.append('<div class="tw"><table><thead><tr>')
            out += [f"<th>{inline(c)}</th>" for c in head]
            out.append("</tr></thead><tbody>")
            for row in body:
                out.append("<tr>")
                out += [f"<td>{inline(c)}</td>" for c in row]
                out.append("</tr>")
            out.append("</tbody></table></div>")
            continue

        if block_start and re.match(r"^(\s*)([-*])\s+(.*)$", line):
            i = _list(lines, i, out, ordered=False)
            block_start = False
            continue

        if block_start and re.match(r"^(\s*)(\d+)\.\s+(.*)$", line):
            i = _list(lines, i, out, ordered=True)
            block_start = False
            continue

        para, i = _wrapped(lines, i)
        out.append(f"<p>{inline(para)}</p>")
        block_start = False

    return "\n".join(out), title


def _wrapped(lines: list[str], i: int) -> tuple[str, int]:
    """Join a hard-wrapped paragraph into one logical line."""
    buf = [lines[i].strip()]
    i += 1
    while i < len(lines) and lines[i].strip() and not re.match(
        r"^(#{1,6}\s|\||-{3,}$|```)", lines[i]
    ):
        buf.append(lines[i].strip())
        i += 1
    return " ".join(buf), i


def _list(lines: list[str], i: int, out: list[str], *, ordered: bool) -> int:
    tag = "ol" if ordered else "ul"
    pattern = r"^(\s*)\d+\.\s+(.*)$" if ordered else r"^(\s*)[-*]\s+(.*)$"
    out.append(f"<{tag}>")
    while i < len(lines):
        m = re.match(pattern, lines[i])
        if not m:
            break
        text = m.group(2)
        i += 1
        # continuation lines of the same item
        while i < len(lines) and re.match(r"^\s{2,}\S", lines[i]) and not re.match(
            pattern, lines[i]
        ):
            text += " " + lines[i].strip()
            i += 1
        out.append(f"<li>{inline(text)}</li>")
    out.append(f"</{tag}>")
    return i


# --------------------------------------------------------------------------- #
# assets
# --------------------------------------------------------------------------- #

def b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def font_css(inline_fonts: bool) -> str:
    faces = []
    for family, style, weight, filename, rng in FONT_FILES:
        if inline_fonts:
            src = f"url(data:font/woff2;base64,{b64(STATIC / 'fonts' / filename)})"
        else:
            src = f"url(../assets/{filename})"
        faces.append(
            f'@font-face{{font-family:"{family}";font-style:{style};'
            f"font-weight:{weight};font-display:swap;"
            f'src:{src} format("woff2");unicode-range:{RANGES[rng]};}}'
        )
    return "\n".join(faces)


CSS = """
:root{
  --dentalia-primary:#89c8ac; --dentalia-primary-strong:#56b088;
  --dentalia-surface:#f3f9f7; --dentalia-surface-2:#e9f5f0; --dentalia-surface-3:#f2f9f6;
  --dentalia-ink:#33312b; --dentalia-muted:#464646; --dentalia-border:#d8ddda;
  --dentalia-amber:#ffb354; --dentalia-danger:#d64545;
  --radius-lg:10px; --radius-md:8px; --radius-sm:6px;
  --font-heading:"Sora",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --font-body:"Work Sans",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
}
*{box-sizing:border-box}
body{margin:0;background:#fff;color:var(--dentalia-ink);font-family:var(--font-body);
  font-size:15px;line-height:1.62;-webkit-font-smoothing:antialiased}
h1,h2,h3,h4,.brand-word{font-family:var(--font-heading);font-weight:700;
  letter-spacing:-0.01em;color:var(--dentalia-ink)}
a{color:var(--dentalia-primary-strong)}
.layout{display:flex;align-items:flex-start;min-height:100vh}
.sidebar{flex:0 0 216px;width:216px;align-self:stretch;position:sticky;top:0;
  max-height:100vh;overflow-y:auto;padding:14px 10px 24px;
  background:var(--dentalia-surface);border-right:1px solid var(--dentalia-border)}
.sidebar .brand{display:block;padding:0 6px 14px;text-decoration:none}
.sidebar .logo{height:24px;display:block;margin-bottom:6px}
.sidebar .brand-word{font-size:13px;line-height:1.25;display:block}
.sidebar nav{display:flex;flex-direction:column;gap:2px}
.sidebar .nav-label{margin:14px 0 3px;padding:0 6px;font-size:10px;font-weight:700;
  letter-spacing:.08em;text-transform:uppercase;color:var(--dentalia-muted)}
.sidebar .nav-label:first-child{margin-top:0}
.sidebar nav a{padding:5px 8px;border-radius:var(--radius-sm);color:var(--dentalia-ink);
  text-decoration:none;font-weight:600;font-size:13px}
.sidebar nav a:hover{background:var(--dentalia-surface-2)}
.sidebar nav a.active{background:var(--dentalia-primary);color:#1e2a24}
.lang{margin:18px 6px 0;font-size:12px;color:var(--dentalia-muted)}
.lang a{font-weight:600}
main{flex:1;min-width:0;padding:26px 32px 80px}
.doc{max-width:44rem}
h1{font-size:30px;line-height:1.15;margin:0 0 8px}
h2{font-size:20px;margin:38px 0 10px;padding-top:20px;
  border-top:1px solid var(--dentalia-border)}
h3{font-size:15px;margin:24px 0 8px}
h4{font-size:14px;margin:18px 0 6px;color:var(--dentalia-muted)}
p{margin:0 0 12px}
ul,ol{margin:0 0 12px;padding-left:20px}
li{margin:4px 0}
strong{font-weight:600}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.9em;
  background:var(--dentalia-surface-2);padding:1px 5px;border-radius:4px}
hr{border:0;border-top:1px solid var(--dentalia-border);margin:26px 0}
table{width:100%;border-collapse:collapse;font-size:14px;margin:0 0 18px}
th,td{text-align:left;vertical-align:top;padding:7px 8px;
  border-bottom:1px solid var(--dentalia-border)}
th{color:var(--dentalia-muted);font-weight:600;font-size:11px;text-transform:uppercase;
  letter-spacing:.04em}
tbody tr:last-child td{border-bottom:0}
.tw{overflow-x:auto}
.page{margin:0 0 64px}
.page + .page{border-top:2px solid var(--dentalia-border);padding-top:32px}
@media (max-width:820px){
  .layout{display:block}
  .sidebar{position:static;width:auto;max-height:none;border-right:0;
    border-bottom:1px solid var(--dentalia-border)}
  main{padding:20px 18px 60px}
}
@media print{
  .sidebar{display:none}
  body{font-size:10.5pt;color:#000}
  main{padding:0}
  .doc{max-width:none}
  h1,h2{page-break-after:avoid}
  table,.tw,ul,ol{page-break-inside:avoid}
  .page{page-break-before:always}
  a{color:#000;text-decoration:none}
}
"""


def other_lang_built(lang: str) -> bool:
    """Only offer the language switch if the other language has sources.

    Building one language alone is normal during a translation pass, and a
    switcher pointing at a directory that was never generated is a 404 in the
    reader's face."""
    return bool(sources("sl" if lang == "en" else "en"))


def nav_html(lang: str, current: str, depth: int) -> str:
    up = "../" * depth
    rows = []
    for label, items in NAV:
        if label:
            rows.append(f'<p class="nav-label">{label}</p>')
        for name, stem in items:
            cls = ' class="active"' if stem == current else ""
            rows.append(f'<a href="{up}{stem}.html"{cls}>{name}</a>')
    other = "sl" if lang == "en" else "en"
    if other_lang_built(lang):
        rows.append(
            f'<p class="lang"><a href="{up}../{other}/{current}.html">'
            f'{"Slovensko" if other == "sl" else "English"}</a></p>'
        )
    return "\n".join(rows)


def shell(*, lang: str, title: str, body: str, nav: str, css: str, logo: str,
          fav: str, up: str) -> str:
    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="icon" href="data:image/svg+xml;base64,{fav}">
{css}
</head>
<body>
<div class="layout">
  <aside class="sidebar">
    <a class="brand" href="{up}glossary.html">
      <img class="logo" src="{logo}" alt="Dentalia">
      <span class="brand-word">{TITLES[lang]}</span>
    </a>
    <nav>
{nav}
    </nav>
  </aside>
  <main><div class="doc">
{body}
  </div></main>
</div>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #

def sources(lang: str) -> list[Path]:
    files = sorted(GUIDE.glob("*.md")) + sorted((GUIDE / "pages").glob("*.md"))
    sl = [f for f in files if f.name.endswith(".sl.md")]
    return sl if lang == "sl" else [f for f in files if f not in sl]


def stem_of(path: Path) -> str:
    name = path.name.removesuffix(".sl.md").removesuffix(".md")
    return f"pages/{name}" if path.parent.name == "pages" else name


def build(lang: str, *, check: bool) -> tuple[int, list[str]]:
    files = sources(lang)
    if not files:
        return 0, [f"no {lang} sources found"]

    problems: list[str] = []
    rendered: list[tuple[str, str, str]] = []  # stem, title, html
    for f in files:
        body, title = render(f.read_text(), problems)
        rendered.append((stem_of(f), title or stem_of(f), body))

    if check:
        return len(rendered), problems

    out = OUT / lang
    if out.exists():
        shutil.rmtree(out)
    (out / "pages").mkdir(parents=True)

    assets = OUT / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    for _, _, _, filename, _ in FONT_FILES:
        shutil.copyfile(STATIC / "fonts" / filename, assets / filename)
    shutil.copyfile(STATIC / "img" / "dentalia-logo.png", assets / "dentalia-logo.png")
    (assets / "guide.css").write_text(font_css(False) + CSS)

    fav = b64(STATIC / "img" / "favicon.svg")
    for stem, title, body in rendered:
        depth = 1 if stem.startswith("pages/") else 0
        up = "../" * depth
        (out / f"{stem}.html").write_text(shell(
            lang=lang, title=f"{title} — Dentalia", body=body,
            nav=nav_html(lang, stem, depth),
            css=f'<link rel="stylesheet" href="{up}../assets/guide.css">',
            logo=f"{up}../assets/dentalia-logo.png", fav=fav, up=up,
        ))

    # single-file bundle: assets inlined, every page concatenated
    logo_uri = "data:image/png;base64," + b64(STATIC / "img" / "dentalia-logo.png")
    order = [s for _, items in NAV for _, s in items]
    ranked = sorted(rendered, key=lambda r: order.index(r[0]) if r[0] in order else 99)
    joined = "\n".join(f'<section class="page" id="{s}">{b}</section>'
                       for s, _, b in ranked)
    bundle = GUIDE / f"dentalia-guide-{lang.upper()}.html"
    bundle.write_text(shell(
        lang=lang, title=f"Dentalia {TITLES[lang]}", body=joined,
        nav="\n".join(f'<a href="#{s}">{t}</a>' for s, t, _ in ranked),
        css=f"<style>\n{font_css(True)}\n{CSS}\n</style>",
        logo=logo_uri, fav=fav, up="",
    ))
    return len(rendered), problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lang", choices=["en", "sl"], action="append",
                    help="build one language (repeatable); default both")
    ap.add_argument("--check", action="store_true",
                    help="parse only, write nothing")
    args = ap.parse_args()

    failed = False
    for lang in args.lang or ["en", "sl"]:
        try:
            n, problems = build(lang, check=args.check)
        except FileNotFoundError as exc:
            print(f"{lang}: missing asset — {exc}", file=sys.stderr)
            failed = True
            continue
        verb = "parsed" if args.check else "rendered"
        print(f"{lang}: {verb} {n} pages")
        for p in problems:
            print(f"  unsupported: {p}", file=sys.stderr)
            failed = True
    if not args.check and not failed:
        print(f"output: {OUT}/  and  {GUIDE}/dentalia-guide-*.html")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
